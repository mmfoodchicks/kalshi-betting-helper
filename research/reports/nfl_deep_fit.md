# NFL deep engine tables -- fit report (2026-10-16 12:36 UTC)
Seasons [2023, 2024, 2025, 2026]: 935 games, 171,409 play rows.

## Real-game validation targets (regular season)
- reg_games: 896
- pts_per_team_game: 22.623
- margin_sd: 14.232
- total_sd: 13.671
- home_margin_mean: 2.038
- home_win_rate: 0.5402
- ot_rate: 0.0558
- tie_rate: 0.0022
- drives_per_team_game: 10.062
- snaps_per_team_game: 66.882
- td_per_drive: 0.2378
- fg_per_drive: 0.1695
- punt_per_drive: 0.3787
- to_per_drive: 0.1806
- three_and_out_per_drive: 0.2206

## League rates (offence)
- pts_pg: 22.6228
- snaps_pg: 66.8817
- plays_pg: 62.8566
- pass_rate: 0.5771
- comp: 0.6401
- ypa: 6.8599
- ypc_pass: 10.717
- sack: 0.0694
- int: 0.0222
- ypc: 4.2477
- fum: 0.0056
- pen: 0.0463
- ypp: 5.2678
- sec_per_snap: 38.755
- punt_net_ytg: 74.4608
- ko_ret_ytg: 70.902

## Field goals: logit P(good) = 5.89602 + -0.095276 x distance (n=3710); blocked [59, 3769]
- 15-19 yds: 40/43 = 0.930
- 20-24 yds: 352/355 = 0.992
- 25-29 yds: 491/505 = 0.972
- 30-34 yds: 504/524 = 0.962
- 35-39 yds: 459/491 = 0.935
- 40-44 yds: 427/519 = 0.823
- 45-49 yds: 392/514 = 0.763
- 50-54 yds: 376/510 = 0.737
- 55-59 yds: 139/206 = 0.675
- 60-64 yds: 16/32 = 0.500
- 65-69 yds: 5/11 = 0.455

## Extra points [4056, 4269]  two-point success [213, 441]  kneels [1494, 1494]  punt return TD [25, 7134]  punts blocked [37, 7171]

## Matchup exponents (own offence, opposing defence); K = games of shrinkage
- ypp: league 5.2653  K=5.92 (median of [2023, 2024, 2025]; by season {'2023': 6.44, '2024': 5.28, '2025': 5.92, '2026': 7.32})  raw (c,a,b,r2,n)=(-0.53155, 0.7624, 0.5447, 0.135, 1776)  shrunk=(-1.41048, 1.0784, 0.7564, 0.1364, 1776)
- ypa: league 6.84944  K=9.17 (median of [2023, 2024, 2025]; by season {'2023': 10.51, '2024': 9.17, '2025': 7.86, '2026': 11.98})  raw (c,a,b,r2,n)=(-0.25821, 0.6907, 0.4327, 0.0925, 1775)  shrunk=(-1.55053, 1.1174, 0.6758, 0.0933, 1775)
- ypc: league 4.26062  K=22.0 (median of [2023, 2024, 2025]; by season {'2023': 22.0, '2024': 11.72, '2025': 30.13, '2026': 6.75})  raw (c,a,b,r2,n)=(0.13661, 0.4785, 0.3808, 0.038, 1775)  shrunk=(-0.99957, 0.9719, 0.6704, 0.0361, 1775)
- pts: league 22.56434  K=6.94 (median of [2023, 2024, 2025]; by season {'2023': 6.94, '2024': 5.06, '2025': 7.2, '2026': 12.18})  raw (c,a,b,r2,n)=(-0.45973, 0.6738, 0.4457, 0.0801, 1759)  shrunk=(-2.26874, 1.0252, 0.6723, 0.0851, 1759)
- ypcomp: league 10.70445  K=13.63 (median of [2023, 2024, 2025]; by season {'2023': 15.79, '2024': 10.5, '2025': 13.63, '2026': 18.76})  raw (c,a,b,r2,n)=(0.0713, 0.5921, 0.3714, 0.0597, 1775)  shrunk=(-1.97517, 1.1327, 0.6929, 0.0604, 1775)
- comp: league 0.63983  K=11.52 (median of [2023, 2024, 2025]; by season {'2023': 25.83, '2024': 11.52, '2025': 11.2, '2026': 62.66})  raw (c,a,b,r2,n)=(0.00213, 0.5631, 0.4933, 0.0639, 1776)  shrunk=(-0.55368, 1.1115, 0.913, 0.0664, 1776)
- fum: league 0.00542  K=84371.51 (median of [2023, 2024, 2025]; by season {'2023': 49.95, '2024': 84371.51, '2025': 115084.49, '2026': 146911.76})  raw (c,a,b,r2,n)=(-4.148, 0.0211, 0.0587, 0.0075, 1700)  shrunk=(-3.03613, 0.3134, -0.018, 0.0166, 1700)
- pen: league 0.04402  K=250.99 (median of [2023, 2024, 2025]; by season {'2023': 50.27, '2024': 250.99, '2025': 1232665.4, '2026': 12.01})  raw (c,a,b,r2,n)=(-0.56787, 0.4401, 0.4413, 0.1674, 1776)  shrunk=(-0.2255, 0.4657, 0.5328, 0.1821, 1776)
- pass_rate: league 0.577  K=8.7 (median of [2023, 2024, 2025]; by season {'2023': 8.7, '2024': 4.66, '2025': 9.53, '2026': 4.13})  raw (c,a,b,r2,n)=(-0.05302, 0.671, 0.53, 0.12, 1776)  shrunk=(-0.22766, 0.9875, 0.7788, 0.1225, 1776)
- sack: league 0.06974  K=7.85 (median of [2023, 2024, 2025]; by season {'2023': 6.83, '2024': 12.28, '2025': 7.85, '2026': 15.54})  raw (c,a,b,r2,n)=(-0.7024, 0.6, 0.1956, 0.0625, 1774)  shrunk=(0.76489, 1.0094, 0.3554, 0.0646, 1774)
- int: league 0.02232  K=26.45 (median of [2023, 2024, 2025]; by season {'2023': 143.5, '2024': 19.85, '2025': 26.45, '2026': 13.96})  raw (c,a,b,r2,n)=(-2.68468, 0.1228, 0.1258, 0.0139, 1736)  shrunk=(-1.09359, 0.3874, 0.2847, 0.0127, 1736)
- points vs yards/play elasticity (c, slope, r2, n): (0.63465, 1.4516, 0.4134, 1775)

## Year-over-year persistence of team unit rates (slope of next season's deviation on this season's)
- off.pass_rate: slope 0.3846  n=64 team-pairs over 2 season pairs
- off.comp: slope 0.3268  n=64 team-pairs over 2 season pairs
- off.ypcomp: slope 0.3912  n=64 team-pairs over 2 season pairs
- off.sack: slope 0.2443  n=64 team-pairs over 2 season pairs
- off.int: slope 0.0438  n=64 team-pairs over 2 season pairs
- off.ypc: slope 0.2584  n=64 team-pairs over 2 season pairs
- off.fum: slope -0.0595  n=64 team-pairs over 2 season pairs
- off.pen: slope 0.3975  n=64 team-pairs over 2 season pairs
- off.ypp: slope 0.4162  n=64 team-pairs over 2 season pairs
- off.sec_per_snap: slope 0.2461  n=64 team-pairs over 2 season pairs
- off.punt_ytg: slope -0.0826  n=64 team-pairs over 2 season pairs
- off.ko_ytg: slope -0.0546  n=64 team-pairs over 2 season pairs
- def.pass_rate: slope 0.2547  n=64 team-pairs over 2 season pairs
- def.comp: slope 0.249  n=64 team-pairs over 2 season pairs
- def.ypcomp: slope 0.041  n=64 team-pairs over 2 season pairs
- def.sack: slope 0.1182  n=64 team-pairs over 2 season pairs
- def.int: slope 0.0287  n=64 team-pairs over 2 season pairs
- def.ypc: slope 0.1444  n=64 team-pairs over 2 season pairs
- def.fum: slope 0.0866  n=64 team-pairs over 2 season pairs
- def.pen: slope 0.1778  n=64 team-pairs over 2 season pairs
- def.ypp: slope 0.1615  n=64 team-pairs over 2 season pairs
- def.sec_per_snap: slope None  n=0 team-pairs over 2 season pairs
- def.punt_ytg: slope None  n=0 team-pairs over 2 season pairs
- def.ko_ytg: slope None  n=0 team-pairs over 2 season pairs

## Kickoff field position by rules era (receiver's yards to go, mean)
- 2023: n=2834 mean ytg 74.53  return TD [5, 2839]
- 2024: n=2951 mean ytg 69.87  return TD [5, 2956]
- 2025+: n=3739 mean ytg 69.06  return TD [7, 3746]
