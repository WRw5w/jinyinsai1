# Normal group search

`normal_group_search.py` is a local physical optimization branch. It retains
all 4,999 valid source orders, the existing net-length interpretation, the
physical bed bounds, a per-order surplus ceiling of 5%, and at most 100 rounds
per output scheme. It does not use official-scoring or serialization loopholes.

The script first decomposes input schemes into actual connected components:
two orders are connected only if they share a cutting round, directly or
transitively. It can therefore repack rounds from distinct original schemes
without treating their administrative boundaries as physical restrictions.
Order components are joined conservatively, and a candidate that would create
more than 100 rounds in a component is rejected. Final schemes are recombined
with the existing whole-component packer.

For a selected set of rounds, it derives lower and upper piece counts from
deliveries in the other rounds. It tries common bundle sizes and alternative
round counts. Two-round splitting uses exact bounded subset sum in millimetres
to minimize billet counts for a fixed piece vector and bundle size. The
multi-round version enumerates small billet-count partitions and uses bounded
subset sum repeatedly to fill their capacities. This is a heuristic for the
joint problem, not a proof of global optimality. Input lengths that cannot be
represented exactly in millimetres are not silently rounded by the subset-sum
routines.

The source data and independent feasibility checker remain unchanged. Every
final result is checked by both `solver.validate_plan` and `platform_check`.
The reported score is the existing local estimate, not an official score.

Example bounded job using the local blocking process watcher:

```powershell
python -X utf8 task_watcher.py --run-dir runs/normal_group_job/watch --timeout 360 -- python -X utf8 normal_group_search.py --initial runs/normal_heterogeneous/result.json --output runs/normal_group_job --seconds 180 --source-rounds 8
```

For 12-round neighbourhoods, `--source-rounds 12 --same-parallel` preferentially
samples rows with a common current bundle size within a component. The timer
limits the neighbourhood search. Loading and the final refinement/checks add
to the total recorded elapsed time.

`normal_component_pack.py` is the deterministic large-neighbourhood companion.
It selects every round with the same bundle size in a complete order component
and jointly repacks that set. It enumerates candidate round counts from the
physical capacity lower bound through the existing number of rounds. Because
it only removes or rearranges rounds inside an existing component, it preserves
the 100-round ceiling. Its `--initial`, `--output`, and `--seconds` options have
the same meaning as the stochastic script.

`test_normal_group_search.py` checks the two-round result against exhaustive
enumeration on small mixed-length cases, rejects unsupported precision, and
checks piece conservation and physical length bounds for multi-round packing.

Outputs are `result.json`, `config.json`, `data_audit.json`, and `report.json`;
the report includes the initial and final metrics, accepted changes, rejected
component unions, and the independent validation result. A checkpoint is
intermediate and should not be substituted for the final validated result.
