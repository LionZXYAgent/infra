"""Keep the owner-approved meal plan and both rollout configurations equal."""
import json
from datetime import date, timedelta
from pathlib import Path
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_PORTIONS = [
    ('2026-11-09', [43, 43, 44], [0, 0, 0], [15, 15, 16], [0, 0, 0]),
    ('2026-11-20', [33, 33, 34], [7, 7, 6], [16, 16, 16], [0, 0, 0]),
    ('2026-11-23', [22, 21, 22], [13, 14, 13], [17, 17, 18], [0, 0, 0]),
    ('2026-11-26', [11, 11, 11], [20, 20, 20], [18, 18, 19], [0, 0, 0]),
    ('2026-11-28', [0, 0, 0], [28, 28, 29], [19, 19, 19], [0, 0, 0]),
    ('2026-11-30', [0, 0, 0], [28, 28, 29], [14, 14, 15], [5, 5, 5]),
    ('2026-12-03', [0, 0, 0], [28, 28, 29], [10, 9, 10], [10, 10, 10]),
    ('2026-12-06', [0, 0, 0], [28, 28, 29], [5, 4, 5], [15, 15, 15]),
    ('2026-12-09', [0, 0, 0], [28, 28, 29], [0, 0, 0], [20, 20, 20]),
]


class AcornNutritionTransitionTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT / 'apps/acorn-care-bot/config.json').read_text())
        self.plan = self.config['nutrition_plan']

    def test_exact_meal_portions_and_stage_boundaries(self):
        stages = self.plan['stages']
        self.assertEqual([stage['start'] for stage in stages], [row[0] for row in EXPECTED_PORTIONS])
        for stage, expected in zip(stages, EXPECTED_PORTIONS):
            for kind, amounts in zip(('wet', 'wet_new', 'dry', 'dry_new'), expected[1:]):
                self.assertEqual(stage[kind], amounts, (stage['start'], kind))
                self.assertEqual(stage['daily_' + kind], sum(amounts), (stage['start'], kind))
        self.assertEqual(self.config['stages'][2:], stages)
        self.assertFalse(any(stage.get('provisional') for stage in self.config['stages']))
        self.assertNotIn('week_5_plus', [stage['id'] for stage in self.config['stages']])

    def test_exact_products_package_sizes_and_approval(self):
        self.assertEqual(self.plan['id'], self.config['nutrition_plan_id'])
        self.assertEqual(self.plan['id'], 'nutrition-2026-11-09-r3')
        self.assertEqual(self.plan['approved_message'], 'telegram_-1004424243504_602')
        self.assertEqual(self.plan['approved_by'], 'palmette')
        self.assertEqual(self.plan['approved_at'], '2026-11-09T10:47:58+00:00')
        self.assertEqual(self.plan['effective'], '2026-11-09')
        self.assertEqual(self.config['food_products'], self.plan['food_products'])
        products = self.plan['food_products']
        self.assertEqual({key: product['name'] for key, product in products.items()}, {
            'wet': 'Royal Canin Gastrointestinal Kitten Wet',
            'wet_new': 'Royal Canin Kitten in Jelly',
            'dry': 'Royal Canin Gastrointestinal Kitten Dry',
            'dry_new': 'Royal Canin Kitten Dry'})
        self.assertEqual(products['wet']['pack_grams'], 195)
        self.assertEqual(products['wet_new']['pack_grams'], 85)
        self.assertEqual(len({product['button_label'] for product in products.values()}), 4)

    def test_eight_advance_notices_at_1930_and_stage_morning_flags(self):
        stages = [stage for stage in self.plan['stages'] if stage.get('advance_notice')]
        self.assertEqual([(date.fromisoformat(stage['start']) - timedelta(days=1)).isoformat()
                          for stage in stages],
                         ['2026-11-19', '2026-11-22', '2026-11-25', '2026-11-27',
                          '2026-11-29', '2026-12-02', '2026-12-05', '2026-12-08'])
        self.assertEqual(self.plan['notice_time'], '19:30')
        self.assertEqual(self.config['nutrition_notice_time'], '19:30')
        self.assertTrue(all(stage['morning_notice'] is True for stage in stages))
        self.assertFalse(self.plan['stages'][0].get('morning_notice', False))

    def test_schedule_repeats_and_single_writer_stay_and_care_matches_owner(self):
        self.assertEqual(self.config['timezone'], 'Europe/London')
        self.assertEqual([slot['time'] for slot in self.config['slots']], ['07:00', '14:00', '19:00'])
        self.assertEqual(self.config['mention'], '@palmette @LionZXY')
        self.assertEqual(self.config['food_transfer_policy'], 'add_full')
        self.assertIs(self.config['automatic_repeats'], False)
        self.assertIs(self.config['bowls_morning_only'], False)
        self.assertEqual(self.config['slots'][2]['cleaning_tasks'], ['litter', 'bowls', 'water'])
        deployment = yaml.safe_load((ROOT / 'apps/acorn-care-bot/deployment.yaml').read_text())
        self.assertEqual(deployment['spec']['replicas'], 1)
        self.assertEqual(deployment['spec']['strategy'], {'type': 'Recreate'})

    def test_slot_labels_match_owner_and_evening_water_is_a_check(self):
        self.assertFalse(self.config['bowls_morning_only'])
        for slot in self.config['slots']:
            change = self.plan['care_slots'][slot['key']]
            self.assertEqual(slot['cleaning_tasks'], change['cleaning_tasks'])
            self.assertEqual(slot['cleaning_labels'], change['cleaning_labels'])
        self.assertEqual(self.config['slots'][2]['cleaning_labels']['water'],
                         'Проверить питьевую воду.')
        self.assertNotIn('Loaf', json.dumps(self.config))

    def test_suspended_suite_uses_identical_food_plan_without_activation(self):
        release = yaml.safe_load((ROOT / 'apps/telegram-bot-suite/helmrelease.yaml').read_text())
        self.assertIs(release['spec']['suspend'], True)
        self.assertEqual(release['spec']['values']['replicas'], 0)
        workers = {worker['id']: worker for worker in release['spec']['values']['workers']}
        self.assertEqual(json.loads(workers['acorn']['config']['config.json']), self.config)
        self.assertEqual(workers['acorn']['persistence'], {'existingClaim': 'acorn-care-bot-state'})
        self.assertEqual(workers['greenwich']['route']['topic_id'], 45)
        self.assertFalse(workers['greenwich']['route']['receives_updates'])


if __name__ == '__main__':
    unittest.main()
