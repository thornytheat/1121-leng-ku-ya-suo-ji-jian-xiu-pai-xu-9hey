"""本班告警阈值的分档决策。

结合班次复盘为当前班次选择告警阈值：只用交班前已验修的样本比较各档
漏报与白检，迟到的验修留到下一班；会挤掉旧阈值下已排上急修的档位不
采用；保住急修的档位里先少漏报，漏报相同再少白检；没有已验修的样本
就沿用旧阈值。
"""

from __future__ import annotations

from .algorithm import verified_observations
from .contracts import parse

DEFAULT_CANDIDATES = (.35, .5, .65, .8, .95)


def select_threshold(rows, *, as_of, old_threshold, displaced_urgent, candidates=DEFAULT_CANDIDATES):
    """选定本班告警阈值并保留逐档证据。

    displaced_urgent 按阈值给出该档重算排程后被挤掉的急修机组；旧阈值
    必然保住自己排上的急修。返回的 candidates 逐档记录漏报、白检与急修
    挤占，selected_threshold 是本班应采用的一档。
    """
    cuts = sorted(set(candidates) | {old_threshold})
    if not all(0 < cut < 1 for cut in cuts):
        raise ValueError('INVALID_THRESHOLD_CANDIDATES')
    verified, exclusions = verified_observations([parse(row) for row in rows], as_of)
    graded = []
    for cut in cuts:
        displaced = sorted(displaced_urgent.get(cut, ()))
        row = {'threshold': cut, 'keeps_urgent': not displaced, 'displaced_urgent': displaced}
        if verified:
            row['missed_faults'] = sum(1 for item in verified if item.confirmed and item.score < cut)
            row['wasted_checks'] = sum(1 for item in verified if not item.confirmed and item.score >= cut)
        else:
            row['missed_faults'] = row['wasted_checks'] = None
        graded.append(row)
    if not verified:
        return {'verified_samples': 0, 'excluded': exclusions, 'candidates': graded,
                'selected_threshold': old_threshold, 'reason': 'NO_VERIFIED_SAMPLES'}
    viable = [row for row in graded if row['keeps_urgent']]
    if not viable:
        raise ValueError('NO_URGENT_SAFE_THRESHOLD')
    best = min(viable, key=lambda row: (row['missed_faults'], row['wasted_checks'],
                                        row['threshold'] != old_threshold, row['threshold']))
    return {'verified_samples': len(verified), 'excluded': exclusions, 'candidates': graded,
            'selected_threshold': best['threshold'],
            'reason': 'KEPT_INCUMBENT' if best['threshold'] == old_threshold else 'ADOPTED_CANDIDATE'}
