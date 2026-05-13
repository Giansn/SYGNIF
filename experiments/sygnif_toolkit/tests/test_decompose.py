import pytest
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from edge_attrib.decompose import process_fills

def test_decompose_components():
    rts = process_fills("fixtures/fills.jsonl")
    assert len(rts) > 0

    for rt in rts:
        # Check that residual is within $0.01 limit
        assert abs(rt['residual']) <= 0.01, f"Residual {rt['residual']} too large"

def test_report_generation():
    # just testing it doesn't crash on execution
    rts = process_fills("fixtures/fills.jsonl")
    from edge_attrib.report import generate_report
    generate_report(rts)
