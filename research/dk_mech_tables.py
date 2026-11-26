"""Phase A as Markdown tables, real beside the model field, one column a contest
(research/reports/dk_mech_tables.md is this script run on the two classic
contests and then on the six showdowns):

    python3 research/dk_mech_tables.py <contest id> [...]

Reads research/data/dk_mech/<id>.json from the repository root. Every row is a
figure research/dk_mech.py defines. "model" is the served field model through
the same code: classic, the rebuilt optimizer field; showdown, production's
softmax rule on the universe.
"""
import json
import sys

D = 'research/data/dk_mech/'
ids = sys.argv[1:]
X = {i: json.load(open(D + i + '.json')) for i in ids}
def row(label, fn):
    print('| ' + label + ' | ' + ' | '.join(str(fn(X[i])) for i in ids) + ' |')
def g(d, *ks):
    for k in ks:
        d = d[k]
    return d
print('| | ' + ' | '.join(X[i]['label'] for i in ids) + ' |'); print('|---' * (len(ids) + 1) + '|')
row('entries with a lineup', lambda d: f"{d['entries_with_lineup']:,}")
row('entrants', lambda d: f"{g(d,'real','users','entrants'):,}")
row('C real', lambda d: '%.3e' % g(d, 'real', 'duplication', 'distinct_pair_collision'))
row('C model (same code)', lambda d: '%.3e' % g(d, 'model_field', 'sampled', 'duplication', 'distinct_pair_collision'))
row('C real on model support', lambda d: ('%.3e' % g(d, 'model_field', 'real_on_the_same_support', 'duplication', 'distinct_pair_collision')) if 'real_on_the_same_support' in d['model_field'] else '-')
row('real / model', lambda d: round((g(d, 'model_field', 'real_on_the_same_support', 'duplication', 'distinct_pair_collision') if 'real_on_the_same_support' in d['model_field'] else g(d, 'real', 'duplication', 'distinct_pair_collision')) / g(d, 'model_field', 'sampled', 'duplication', 'distinct_pair_collision'), 1))
row('distinct lineups', lambda d: f"{g(d,'real','duplication','distinct_lineups'):,}")
row('max copies real / model', lambda d: f"{g(d,'real','duplication','max_copies')} / {g(d,'model_field','sampled','duplication','max_copies')}")
row('entries duplicated % real / model', lambda d: f"{g(d,'real','duplication','entries_in_duplicated_lineups_pct')} / {g(d,'model_field','sampled','duplication','entries_in_duplicated_lineups_pct')}")
for b in ('1', '2-5', '6-20', '21-100', '101+'):
    row(f'entries in lineups with {b} copies % real / model', lambda d: f"{g(d,'real','duplication','entries_by_copies_pct')[b]} / {g(d,'model_field','sampled','duplication','entries_by_copies_pct')[b]}")
row('same-user share of duplicate pairs %', lambda d: g(d, 'real', 'users', 'same_user_share_of_duplicate_pairs_pct'))
row('C cross-user only', lambda d: '%.3e' % g(d, 'real', 'users', 'collision_cross_user_only'))
row('entries repeating own lineup %', lambda d: g(d, 'real', 'users', 'entries_repeating_the_entrants_own_lineup_pct'))
for cl in ('1', '2-19', '20-149', '150+'):
    row(f'class {cl}: entries % / in a lineup another entrant holds % / mean copies / within-entrant overlap', lambda d: (lambda v: f"{v['entries_pct']} / {v['their_entries_in_a_lineup_another_entrant_holds_pct']} / {v['their_entries_mean_copies']} / {v['within_entrant_mean_overlap']}")(g(d, 'real', 'users', 'classes')[cl]) if cl in g(d, 'real', 'users', 'classes') else '-')
row('field mean overlap real / model', lambda d: f"{round(g(d,'real','mean_overlap'),3)} / {round(g(d,'model_field','sampled','mean_overlap'),3)}")
def nr(d, who):
    n = g(d, 'real', 'near') if who == 'real' else g(d, 'model_field', 'sampled', 'near')
    return n
row('pair share by overlap (exact; one swap; two swaps) real', lambda d: '; '.join('%.2e' % v for v in nr(d, 'real')['pair_share_overlap'].values()))
row('same, model', lambda d: '; '.join('%.2e' % v for v in nr(d, 'm')['pair_share_overlap'].values()))
row('one-swap pairs per exact pair real / model', lambda d: f"{round(nr(d,'real')['near_to_exact_pair_ratio']['one_swap'],1)} / {round(nr(d,'m')['near_to_exact_pair_ratio']['one_swap'],1)}")
row('entries with ANOTHER entrant within 0 / 1 / 2 swaps % real', lambda d: ' / '.join(str(v) for v in nr(d, 'real')['entries_with_another_USERS_entry_pct'].values()))
row('same, model', lambda d: ' / '.join(str(v) for v in nr(d, 'm')['entries_with_another_entry_pct'].values()))
row('pair |log lift| real / model', lambda d: f"{g(d,'real','pairs','mean_abs_log_lift_weighted')} / {g(d,'model_field','pairs','mean_abs_log_lift_weighted')}")
row('score mean real / model', lambda d: f"{g(d,'real','score_mean')} / {g(d,'model_field','sampled','score_mean')}")
def st(d, who, k):
    s = g(d, 'real', 'structure') if who == 'real' else g(d, 'model_field', 'sampled', 'structure')
    return s.get(k)
for k in ('salary_mean', 'salary_at_cap_pct', 'salary_48000_or_less_pct', 'salary_left_pct', 'stack_pct', 'bring_back_pct', 'templates_pct', 'flex_position_pct', 'max_from_one_team_pct', 'team_split_pct', 'captain_position_pct', 'flex_kickers_and_defenses_pct', 'qb_captain_own_catchers_pct', 'non_qb_captain_with_his_qb_in_flex_pct', 'both_quarterbacks_pct'):
    if st(X[ids[0]], 'real', k) is not None:
        row(k + ' real', lambda d: json.dumps(st(d, 'real', k)).replace('"', ''))
        row(k + ' model', lambda d: json.dumps(st(d, 'm', k)).replace('"', ''))
p0 = g(X[ids[0]], 'real', 'optimizer_proximity')
if 'legal_lineups' in p0:
    row('universe players / lineups / representable %', lambda d: (lambda p: f"{p['universe_players']} / {p['legal_lineups']:,} / {p['representable_entries_pct']}")(g(d, 'real', 'optimizer_proximity')))
    row('copies of OUR optimum (real / production expects)', lambda d: f"{g(d,'real','optimizer_proximity','optimum','real_copies')} / {g(d,'model_field','top_expected_copies_at_N')}")
    for K in ('1', '10', '100', '1000', '10000'):
        row(f'entries in projection top {K} % real / production', lambda d: (lambda v: f"{v['real']} / {v['production_field']}")(g(d, 'real', 'optimizer_proximity', 'entries_in_projection_top_K_pct')[K]))
    for b in ('1', '2-5', '6-20', '21-100', '101+'):
        row(f'{b} copies: in top 1,000 % / median gap pts / $0 left %', lambda d: (lambda v: f"{v['top_1000_pct']} / {v['median_gap_pts']} / {v['salary_left_0_pct']}")(g(d, 'real', 'optimizer_proximity', 'by_copies')[b]) if b in g(d, 'real', 'optimizer_proximity', 'by_copies') else '-')
    for cl in ('1', '2-19', '20-149', '150+'):
        row(f'class {cl}: in top 1,000 % / median gap / $0 left %', lambda d: (lambda v: f"{v['top_1000_pct']} / {v['median_gap_pts']} / {v['salary_left_0_pct']}")(g(d, 'real', 'optimizer_proximity', 'by_user_class')[cl]))
    row('most-copied 20: median projection rank / at $49,800+ / median production expected copies vs real', lambda d: (lambda t: f"{sorted(x['proj_rank'] or 10**9 for x in t)[len(t)//2]} / {sum(1 for x in t if x['salary']>=49800)} of {len(t)} / {sorted(x['production_expected_copies'] or 0 for x in t)[len(t)//2]} vs {sorted(x['copies'] for x in t)[len(t)//2]}")(g(d, 'real', 'most_copied')))
    row('ownership MAE captain / flex (production, same support)', lambda d: f"{g(d,'model_field','ownership_mae_pct_points','captain')} / {g(d,'model_field','ownership_mae_pct_points','flex')}")
    row('joint ownership MAE', lambda d: g(d, 'model_field', 'joint_ownership_mae_pct_points'))
else:
    row('our optimum: proj / copies in the field', lambda d: f"{g(d,'real','optimizer_proximity','optimum','proj')} / {g(d,'real','optimizer_proximity','optimum','real_copies')}")
    row('gap to optimum, quantiles real', lambda d: json.dumps(g(d, 'real', 'optimizer_proximity', 'gap_pts_quantiles')).replace('"', ''))
    row('same, model', lambda d: json.dumps(g(d, 'model_field', 'replica_gap_pts_quantiles')).replace('"', ''))
    row('same, oracle marginals', lambda d: json.dumps(g(d, 'oracle_marginals', 'gap_pts_quantiles')).replace('"', ''))
    row('within 8 / 12 / 20 pts % real', lambda d: ' / '.join(str(g(d, 'real', 'optimizer_proximity', 'entries_within_pts_pct')[k]) for k in ('8', '12', '20')))
    row('same, model', lambda d: ' / '.join(str(g(d, 'model_field', 'replica_entries_within_pts_pct')[k]) for k in ('8', '12', '20')))
    for b in ('1', '2-5', '6-20', '21-100', '101+'):
        row(f'{b} copies: entries / median gap / within 10 pts % / at cap %', lambda d: (lambda v: f"{v['entries']:,} / {v['median_gap_pts']} / {v['within_10_pts_pct']} / {v['salary_at_cap_pct']}")(g(d, 'real', 'optimizer_proximity', 'by_copies')[b]) if b in g(d, 'real', 'optimizer_proximity', 'by_copies') else '-')
    for cl in ('1', '2-19', '20-149', '150+'):
        row(f'class {cl}: median gap / within 10 pts % / at cap %', lambda d: (lambda v: f"{v['median_gap_pts']} / {v['within_10_pts_pct']} / {v['salary_at_cap_pct']}")(g(d, 'real', 'optimizer_proximity', 'by_user_class')[cl]))
    row('most-copied 20: at the cap / median gap / entrants per copy', lambda d: (lambda t: f"{sum(1 for x in t if x['salary']==50000)} of {len(t)} / {sorted(x['proj_gap'] for x in t)[len(t)//2]} / {round(sum(x['entrants'] for x in t)/sum(x['copies'] for x in t),2)}")(g(d, 'real', 'most_copied')))
    row('ownership MAE real top 40: field / opt / casual', lambda d: (lambda v: f"{v['field_pct']} / {v['opt_pct']} / {v['casual_pct']}")(g(d, 'model_field', 'ownership_mae_pct_points', 'real_top_40')))
    row('joint MAE top 30: served / oracle (mean joint)', lambda d: f"{g(d,'model_field','joint_ownership_mae_pct_points_top30')} / {g(d,'oracle_marginals','joint_ownership_mae_pct_points_top30')} ({g(d,'model_field','joint_real_mean_pct_top30')})")
    row('sum of ownership^2 real', lambda d: g(d, 'real', 'ownership_concentration_sum_of_squares'))
    row('ORACLE C / entries duplicated % / max copies', lambda d: (lambda s: f"{'%.3e' % s['distinct_pair_collision']} / {s['entries_in_duplicated_lineups_pct']} / {s['max_copies']}")(g(d, 'oracle_marginals', 'sampled', 'duplication')))
    row('ORACLE pair share by overlap', lambda d: '; '.join('%.2e' % v for v in g(d, 'oracle_marginals', 'sampled', 'near', 'pair_share_overlap').values()))
    row('ORACLE salary mean / at cap / score mean', lambda d: f"{g(d,'oracle_marginals','sampled','structure','salary_mean')} / {g(d,'oracle_marginals','sampled','structure','salary_at_cap_pct')} / {g(d,'oracle_marginals','sampled','score_mean')}")
    row('top 6 owned: real % (served field %)', lambda d: ', '.join(f"{t['name']} {t['real_pct']} ({t.get('field_pct')})" for t in g(d, 'real', 'ownership_top')[:6]))
for b in ('1', '2-5', '6-20', '21-100', '101+'):
    row(f'{b} copies: mean real points / in the top 10% %', lambda d: (lambda v: f"{v['mean_points']} / {v['top_10pct_share_pct']}")(g(d, 'real', 'finish_by_copies')[b]) if b in g(d, 'real', 'finish_by_copies') else '-')
