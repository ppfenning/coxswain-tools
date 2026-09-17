from agent_tools.steward import ceiling_candidates, render_proposal

POLICY = {
    "min_n": 20,
    "window_days_cap": 14,
    "landed_rate_delta_floor": 0.05,
}

ROW = {
    "role": "build",
    "model": "sonnet-5",
    "ceiling": 2.00,
    "censored": False,
    "n_challenger": 20,
    "landed_rate_challenger": 0.91,
    "landed_rate_floor": 0.85,
    "window_days": 10,
    "strict": 1.50,
    "moderate": 1.80,
    "liberal": 2.40,
}


def test_ceiling_candidates_returns_a_candidate_that_clears_the_bar():
    candidates = ceiling_candidates([ROW], POLICY)

    assert candidates == [
        {
            "role": "build",
            "model": "sonnet-5",
            "direction": "raise",
            "current_ceiling": 2.00,
            "proposed_ceiling": 2.40,
            "n": 20,
            "landed_rate_challenger": 0.91,
            "landed_rate_floor": 0.85,
            "window_days": 10,
        }
    ]


def test_ceiling_candidates_excludes_a_row_short_on_n():
    row = {**ROW, "n_challenger": 19}

    assert ceiling_candidates([row], POLICY) == []


def test_ceiling_candidates_excludes_a_row_short_on_window():
    row = {**ROW, "window_days": 15}

    assert ceiling_candidates([row], POLICY) == []


def test_ceiling_candidates_excludes_a_row_whose_landed_rate_delta_is_too_small():
    row = {**ROW, "landed_rate_challenger": 0.87}

    assert ceiling_candidates([row], POLICY) == []


def test_render_proposal_states_the_change_and_table_numbers():
    candidate = ceiling_candidates([ROW], POLICY)[0]

    body = render_proposal(candidate)

    assert "Raise build's ceiling for sonnet-5 from 2.0 to 2.4." in body
    assert "| build | sonnet-5 | 20 | 0.91 | 0.85 | 10 |" in body
