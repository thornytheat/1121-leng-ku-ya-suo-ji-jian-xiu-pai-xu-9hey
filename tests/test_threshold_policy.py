from math import log

import pytest

from chillwatch import pipeline
from chillwatch.shift_review import select_threshold


def closed(unit, score, fault, **updates):
    return {'unit': unit, 'minute': 12, 'measured': 20, 'score': score, 'confirmed': fault,
            'alarm': score >= .8, 'inspected': True} | updates


def test_lower_tier_wins_when_it_misses_fewer_faults():
    rows = [closed('F1', .6, True), closed('F2', .9, False)]
    view = select_threshold(rows, as_of=30, old_threshold=.8, displaced_urgent={})
    assert view['selected_threshold'] == .35
    assert view['reason'] == 'ADOPTED_CANDIDATE'


def test_tie_on_missed_faults_prefers_fewer_wasted_checks():
    rows = [closed('F1', .6, True), closed('F2', .4, False), closed('F3', .9, False)]
    view = select_threshold(rows, as_of=30, old_threshold=.8, displaced_urgent={})
    assert view['selected_threshold'] == .5
    proposals = {row['threshold']: row for row in view['candidates']}
    assert proposals[.35]['wasted_checks'] == 2
    assert proposals[.5]['wasted_checks'] == 1


def test_tier_displacing_urgent_repairs_is_not_adopted():
    rows = [closed('F1', .6, True), closed('F2', .9, False)]
    veto = {.35: ['F-07'], .5: ['F-07']}
    view = select_threshold(rows, as_of=30, old_threshold=.8, displaced_urgent=veto)
    proposals = {row['threshold']: row for row in view['candidates']}
    assert proposals[.35]['keeps_urgent'] is False
    assert proposals[.35]['displaced_urgent'] == ['F-07']
    assert proposals[.35]['missed_faults'] == 0
    assert view['selected_threshold'] == .95


def test_full_tie_keeps_the_incumbent_threshold():
    view = select_threshold([closed('F1', .9, True)], as_of=30, old_threshold=.8, displaced_urgent={})
    assert view['selected_threshold'] == .8
    assert view['reason'] == 'KEPT_INCUMBENT'


def test_late_samples_wait_for_the_next_shift():
    rows = [closed('F1', .6, True, measured=31)]
    view = select_threshold(rows, as_of=30, old_threshold=.8, displaced_urgent={})
    assert view['verified_samples'] == 0
    assert view['selected_threshold'] == .8
    assert view['reason'] == 'NO_VERIFIED_SAMPLES'
    assert all(row['missed_faults'] is None for row in view['candidates'])


def test_uninspected_samples_keep_the_old_threshold():
    rows = [closed('F1', .6, True, inspected=False)]
    view = select_threshold(rows, as_of=30, old_threshold=.8, displaced_urgent={})
    assert view['selected_threshold'] == .8
    assert view['reason'] == 'NO_VERIFIED_SAMPLES'


def test_old_threshold_is_graded_even_when_absent_from_candidates():
    view = select_threshold([closed('F1', .9, True)], as_of=30, old_threshold=.8,
                            displaced_urgent={}, candidates=(.3, .6))
    assert [row['threshold'] for row in view['candidates']] == [.3, .6, .8]


def test_invalid_threshold_candidates_are_rejected():
    with pytest.raises(ValueError, match='INVALID_THRESHOLD_CANDIDATES'):
        select_threshold([], as_of=30, old_threshold=1.2, displaced_urgent={})


def test_contradictory_veto_input_is_rejected():
    veto = {cut: ['F-07'] for cut in (.35, .5, .65, .8, .95)}
    with pytest.raises(ValueError, match='NO_URGENT_SAFE_THRESHOLD'):
        select_threshold([closed('F1', .9, True)], as_of=30, old_threshold=.8, displaced_urgent=veto)


def test_pipeline_adopts_the_review_preferred_threshold():
    station = pipeline.run()
    selection = station['reconciliation']['threshold_selection']
    assert selection['selected_threshold'] == .5
    assert selection['reason'] == 'ADOPTED_CANDIDATE'
    assert station['metrics']['告警阈值'] == .5
    assert station['stages'][2]['records'][0]['threshold'] == .5


def test_pipeline_keeps_urgent_repairs_scheduled():
    station = pipeline.run()
    selection = station['reconciliation']['threshold_selection']
    assert selection['urgent_units'] == ['F-05', 'F-07', 'F-11']
    scheduled = {row['unit'] for row in station['rows'] if row['status'] == '已排检'}
    assert set(selection['urgent_units']) <= scheduled
    assert all(row['keeps_urgent'] for row in selection['candidates'])


def vibration_for(score, center=2.0, scale=.08, midpoint=2.75, gain=1.7):
    return center + scale * (midpoint + log(score / (1 - score)) / gain)


def crowded_fixture():
    history = [
        {'unit': unit, 'family': '螺杆', 'minute': minute, 'pressure_kpa': 1000,
         'vibration': 2.0, 'defrost': False}
        for unit in ('F-01', 'F-02') for minute in (150, 180, 210, 240, 270)
    ]
    meter = [
        *[{'unit': 'F-01', 'minute': minute, 'reported': minute + 1, 'pressure': 1000,
           'scale': 'kPa', 'vibration': vibration_for(.83), 'defrost': False, 'revision': 1}
          for minute in (560, 570, 580)],
        {'unit': 'F-02', 'minute': 560, 'reported': 561, 'pressure': 1000, 'scale': 'kPa',
         'vibration': vibration_for(.7), 'defrost': False, 'revision': 1},
        {'unit': 'F-02', 'minute': 570, 'reported': 571, 'pressure': 1000, 'scale': 'kPa',
         'vibration': vibration_for(.86), 'defrost': False, 'revision': 1},
        {'unit': 'F-02', 'minute': 580, 'reported': 581, 'pressure': 1000, 'scale': 'kPa',
         'vibration': vibration_for(.7), 'defrost': False, 'revision': 1},
    ]
    work = [
        {'unit': 'F-01', 'rack': 'R-1', 'runtime': 30, 'has_backup': True,
         'allowed_start': 600, 'allowed_end': 700},
        {'unit': 'F-02', 'rack': 'R-2', 'runtime': 30, 'has_backup': True,
         'allowed_start': 600, 'allowed_end': 700},
    ]
    outcomes = [
        {'unit': 'F-01', 'minute': 300, 'score': .6, 'alarm': False, 'inspected': True,
         'confirmed': True, 'measured': 400},
        {'unit': 'F-02', 'minute': 301, 'score': .9, 'alarm': True, 'inspected': True,
         'confirmed': False, 'measured': 401},
    ]
    return {'meter': meter, 'history': history, 'work': work, 'closed': outcomes}


def test_lower_tier_crowding_out_urgent_repair_is_vetoed(monkeypatch):
    monkeypatch.setattr(pipeline, 'read_fixture', crowded_fixture)
    station = pipeline.run(capacity=1)
    selection = station['reconciliation']['threshold_selection']
    assert selection['urgent_units'] == ['F-01']
    assert selection['selected_threshold'] == .8
    assert selection['reason'] == 'KEPT_INCUMBENT'
    proposals = {row['threshold']: row for row in selection['candidates']}
    assert proposals[.5]['missed_faults'] == 0
    assert proposals[.5]['keeps_urgent'] is False
    assert proposals[.5]['displaced_urgent'] == ['F-01']
    rows = {row['unit']: row for row in station['rows']}
    assert rows['F-01']['status'] == '已排检'


def test_isolated_spike_over_the_line_is_never_scheduled(monkeypatch):
    monkeypatch.setattr(pipeline, 'read_fixture', crowded_fixture)
    station = pipeline.run(capacity=1)
    rows = {row['unit']: row for row in station['rows']}
    assert rows['F-02']['risk'] == 86
    assert rows['F-02']['status'] == '观察'
    assert rows['F-02']['reason'] == 'PERSISTENCE_NOT_CONFIRMED'
