from __future__ import annotations

import unittest

from draftboard.state.commercial_league_profile import (
    CommercialLeagueProfileError,
    summarize_commercial_league_profile,
    validate_commercial_league_profile,
)


def base_profile(*, model='redraft', sport='baseball', method='snake') -> dict:
    return {
        'league': {
            'league_key': f'pilot.{sport}.2027',
            'name': 'Pilot League',
            'platform': 'manual',
            'sport': sport,
            'league_model': model,
            'season_year': 2027,
            'manager_count': 12,
        },
        'draft': {
            'method': method,
            'execution_mode': 'offline',
            'pick_trades_allowed': True,
        },
        'player_control': {
            'keeper': {
                'enabled': False,
                'count': 0,
                'cost_mode': 'none',
            },
            'contracts': {
                'enabled': False,
                'slots': [],
            },
            'restricted_rights': {
                'enabled': False,
                'label': '',
            },
            'franchise_designation': {'enabled': False},
            'prospect_designation': {'enabled': False},
            'future_pick_trading': False,
            'annual_draft': False,
        },
        'runtime': {
            'db_scope_mode': 'league_key',
        },
    }


class CommercialLeagueProfileV2Tests(unittest.TestCase):
    def test_redraft_does_not_require_contracts(self) -> None:
        profile = base_profile()
        validate_commercial_league_profile(profile)

        summary = summarize_commercial_league_profile(profile)
        self.assertEqual(summary['league_model'], 'redraft')
        self.assertEqual(summary['contract_slots'], 0)

    def test_hockey_is_supported(self) -> None:
        profile = base_profile(sport='hockey')
        validate_commercial_league_profile(profile)

    def test_keeper_requires_keeper_configuration(self) -> None:
        profile = base_profile(model='keeper')

        with self.assertRaisesRegex(
            CommercialLeagueProfileError,
            'keeper.enabled',
        ):
            validate_commercial_league_profile(profile)

    def test_keeper_profile_is_valid(self) -> None:
        profile = base_profile(model='keeper')
        profile['player_control']['keeper'] = {
            'enabled': True,
            'count': 5,
            'cost_mode': 'draft_round',
        }

        validate_commercial_league_profile(profile)

    def test_dynasty_profile_is_valid(self) -> None:
        profile = base_profile(model='dynasty')
        profile['player_control']['future_pick_trading'] = True
        profile['player_control']['annual_draft'] = True

        validate_commercial_league_profile(profile)

    def test_contract_keeper_requires_contract_slots(self) -> None:
        profile = base_profile(model='contract_keeper')

        with self.assertRaisesRegex(
            CommercialLeagueProfileError,
            'contracts.enabled',
        ):
            validate_commercial_league_profile(profile)

    def test_contract_keeper_supports_generic_restricted_rights(self) -> None:
        profile = base_profile(model='contract_keeper')
        profile['player_control']['contracts'] = {
            'enabled': True,
            'slots': [
                {'duration_years': 5, 'count': 1},
                {'duration_years': 4, 'count': 1},
                {'duration_years': 3, 'count': 1},
                {'duration_years': 2, 'count': 1},
            ],
        }
        profile['player_control']['restricted_rights'] = {
            'enabled': True,
            'label': 'Qualifying Offer',
        }

        validate_commercial_league_profile(profile)

    def test_auction_is_independent_of_league_model(self) -> None:
        profile = base_profile(method='auction')
        profile['draft']['starting_budget'] = 260
        profile['draft']['minimum_bid'] = 1

        validate_commercial_league_profile(profile)

    def test_enabled_restricted_rights_require_a_label(self) -> None:
        profile = base_profile(model='contract_keeper')
        profile['player_control']['contracts'] = {
            'enabled': True,
            'slots': [{'duration_years': 3, 'count': 2}],
        }
        profile['player_control']['restricted_rights'] = {
            'enabled': True,
            'label': '',
        }

        with self.assertRaisesRegex(
            CommercialLeagueProfileError,
            'restricted_rights.label',
        ):
            validate_commercial_league_profile(profile)


if __name__ == '__main__':
    unittest.main()
