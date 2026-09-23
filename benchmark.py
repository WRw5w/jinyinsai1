"""Synthetic engineering benchmark, not competition data or leaderboard quality."""
import argparse
import json
import math
import random
import time
from solver import Config, Order, search_10s, validate_plan


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--n', type=int, default=5000)
    ap.add_argument('--seconds', type=float, default=10)
    ap.add_argument('--output', default='benchmark.metrics.json')
    args = ap.parse_args()
    if args.n < 1:
        ap.error('--n must be positive')
    rng = random.Random(20260915)
    orders = []
    for i in range(args.n):
        dia = rng.choice([16, 20, 25])
        size = rng.choice([3, 4, 6, 9, 12])
        pieces = rng.randint(5, 200)
        linear = math.pi * (dia / 1000) ** 2 / 4 * 7850
        orders.append(Order(f'TEST{i}', rng.choice(['S1', 'S2', 'S3']), dia, size,
                            (pieces - 0.25) * size * linear, 7850, pieces))
    cfg = Config()
    started = time.perf_counter()
    plan = search_10s(orders, cfg, seconds=args.seconds)
    elapsed = time.perf_counter() - started
    metrics = validate_plan(plan, orders, cfg)
    metrics.update(elapsed_seconds=elapsed, budget_seconds=args.seconds, data='synthetic_only')
    with open(args.output, 'w', encoding='utf-8') as stream:
        json.dump(metrics, stream, indent=2)
        stream.write('\n')
    print(json.dumps(metrics))


if __name__ == '__main__':
    main()
