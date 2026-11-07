#!/usr/bin/env python3
"""A minimal dependency-free Simlock remote gateway helper for macOS agents.

Requires SIMLOCK_URL (HTTPS origin) and SIMLOCK_TOKEN (gateway-issued agent token).
This tool is for leases and text-only adb exec; use agent-device to upload APKs
on Simlock 0.6.0.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

def env():
    url=os.environ.get("SIMLOCK_URL","").rstrip("/")
    token=os.environ.get("SIMLOCK_TOKEN","")
    if not url or not token:
        raise RuntimeError("Set SIMLOCK_URL and SIMLOCK_TOKEN")
    p=urllib.parse.urlsplit(url)
    if not (p.scheme=="https" or
            (p.scheme=="http" and p.hostname in ("localhost","127.0.0.1","::1"))):
        raise RuntimeError("SIMLOCK_URL must be HTTPS or local loopback HTTP")
    if p.path not in ("","/") or p.query or p.fragment:
        raise RuntimeError("SIMLOCK_URL must be an origin without a path")
    return url,token

def request(method,path,body=None,timeout=75):
    url,token=env()
    data=None if body is None else json.dumps(body).encode()
    headers={"Authorization":"Bearer "+token}
    if data is not None:
        headers["Content-Type"]="application/json"
    req=urllib.request.Request(url+path,headers=headers,data=data,method=method)
    try:
        return urllib.request.urlopen(req,timeout=timeout)
    except urllib.error.HTTPError as e:
        raise RuntimeError("Simlock HTTP %s: %s"%(e.code,e.read(1200).decode(errors="replace"))) from e
    except urllib.error.URLError as e:
        raise RuntimeError("Cannot reach Simlock: %s"%e.reason) from e

def api(method,path,body=None):
    with request(method,path,body) as r:
        data=r.read()
        return json.loads(data) if data else {}

def quoted(lease):
    if not lease or lease.startswith("-"):
        raise RuntimeError("Invalid lease id")
    return urllib.parse.quote(lease,safe="")

def lease(args):
    body={"platform":"android","ttlMs":args.ttl_ms}
    if args.os: body["os"]=args.os
    if args.device: body["device"]=args.device
    if args.image_tag: body["imageTag"]=args.image_tag
    obj=api("POST","/v1/lease-requests",body)
    req=obj["request"]
    reqid=urllib.parse.quote(req["id"],safe="")
    deadline=time.monotonic()+args.wait_seconds
    while req["state"] not in ("granted","failed","cancelled"):
        if time.monotonic()>deadline:
            raise RuntimeError("Request %s is pending; inspect it before retrying"%reqid)
        req=api("GET","/v1/lease-requests/%s?wait=60"%reqid)["request"]
    if req["state"]!="granted":
        raise RuntimeError("Lease request failed: %s"%req.get("error",req["state"]))
    result=req["lease"]
    print(result["id"] if args.id_only else json.dumps(result,indent=2))

def adb(args):
    argv=args.arguments
    if argv and argv[0]=="--": argv=argv[1:]
    if not argv: raise RuntimeError("ADB command required")
    path="/v1/leases/%s/exec"%quoted(args.lease_id)
    exitcode=None
    with request("POST",path,{"tool":"adb","args":argv},timeout=650) as stream:
        event,lines="",[]
        def parse():
            nonlocal exitcode
            if not lines: return
            d=json.loads("\n".join(lines))
            if event=="output":
                dest=sys.stderr if d.get("stream")=="stderr" else sys.stdout
                dest.write(d.get("chunk",""))
                dest.flush()
            elif event=="exit": exitcode=int(d.get("exitCode",1))
            elif event=="error": raise RuntimeError("Remote ADB failed: %s"%d)
        for raw in stream:
            s=raw.decode("utf-8","replace").rstrip("\r\n")
            if not s:
                parse()
                event,lines="",[]
            elif s.startswith("event:"): event=s[6:].strip()
            elif s.startswith("data:"): lines.append(s[5:].lstrip())
        parse()
    if exitcode is None: raise RuntimeError("ADB stream closed without exit")
    return exitcode

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    subs=parser.add_subparsers(dest="action",required=True)
    for cmd in ("identity","status","catalog","leases"):
        subs.add_parser(cmd)
    p=subs.add_parser("lease")
    p.add_argument("--os")
    p.add_argument("--device")
    p.add_argument("--image-tag")
    p.add_argument("--ttl-ms",type=int,default=1800000)
    p.add_argument("--wait-seconds",type=int,default=900)
    p.add_argument("--id-only",action="store_true")
    p=subs.add_parser("adb")
    p.add_argument("lease_id")
    p.add_argument("arguments",nargs=argparse.REMAINDER)
    for cmd in ("renew","release"):
        p=subs.add_parser(cmd)
        p.add_argument("lease_id")
    args=parser.parse_args()
    try:
        if args.action=="lease":
            lease(args)
            return 0
        if args.action=="adb": return adb(args)
        path={"identity":"/v1/identity","status":"/v1/status",
              "catalog":"/v1/catalog?platform=android",
              "leases":"/v1/leases"}.get(args.action)
        if path:
            result=api("GET",path)
        else:
            path="/v1/leases/%s"%quoted(args.lease_id)
            result=api("POST",path+"/renew") if args.action=="renew" else api("DELETE",path)
        print(json.dumps(result,indent=2))
        return 0
    except (RuntimeError,ValueError,OSError,KeyError,TimeoutError) as e:
        print(str(e),file=sys.stderr)
        return 1

if __name__=="__main__":
    sys.exit(main())
