"""Measure output stability across repeated approved requirement runs (roadmap S5a)."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from orchestrator.domain.requirements.stability import render_report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('runs', nargs='+', help='Approved run directories, each containing requirements.md')
    parser.add_argument('--out', help='Also write the Markdown report to this path')
    args = parser.parse_args(argv)
    try:
        report = render_report(args.runs)
    except (ValueError, OSError, json.JSONDecodeError) as error:
        parser.error(str(error))
    print(report)
    if args.out:
        Path(args.out).write_text(report, encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
