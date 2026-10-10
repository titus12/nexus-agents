"""Offline quality gate; missing human review is incomplete, never success."""
import argparse
import json
from pathlib import Path
from orchestrator.requirements_metrics import evaluate


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--fixtures',default='cmd/fixtures/requirements')
    parser.add_argument('--results',required=True)
    parser.add_argument('--offline',action='store_true',required=True)
    parser.add_argument('--mechanisms-only',action='store_true')
    parser.add_argument('--output',help='Optional JSON report path; never modifies task artifacts.')
    args = parser.parse_args(argv)
    report = evaluate(args.fixtures,args.results,args.mechanisms_only)
    encoded = json.dumps(report,ensure_ascii=False,indent=2)
    if args.output:
        Path(args.output).write_text(encoded+'\n',encoding='utf-8')
    print(encoded)
    return 0 if report['status'] in ('PASS','MECHANISMS_ONLY') else 1


if __name__=='__main__':
    raise SystemExit(main())
