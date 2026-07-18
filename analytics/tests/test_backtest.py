import math
from datetime import date

import pytest

from cdlhub_analytics.backtest import Prediction, evaluate


def _p(p: float, won: bool) -> Prediction:
    return Prediction(p=p, won=won, when=date(2019, 1, 1))


def test_golden_metrics() -> None:
    preds = [_p(0.8, True), _p(0.6, False), _p(0.5, True), _p(0.3, False)]
    r = evaluate(preds)
    # brier = (0.04 + 0.36 + 0.25 + 0.09) / 4
    assert math.isclose(r.brier, 0.185)
    expected_ll = -(math.log(0.8) + math.log(0.4) + math.log(0.5) + math.log(0.7)) / 4
    assert math.isclose(r.log_loss, expected_ll)
    assert r.accuracy == 0.75  # 0.5 counts as a predicted win
    assert r.n == 4


def test_calibration_bins_partition_all_predictions() -> None:
    preds = [_p(i / 20, i % 2 == 0) for i in range(21)]  # includes p=1.0 edge
    r = evaluate(preds)
    assert sum(int(b["n"]) for b in r.calibration) == 21
    top = r.calibration[-1]
    assert top["hi"] == 1.0 and int(top["n"]) >= 1


def test_perfectly_calibrated_coin() -> None:
    preds = [_p(0.5, i % 2 == 0) for i in range(100)]
    r = evaluate(preds)
    assert math.isclose(r.brier, 0.25)
    bin5 = next(b for b in r.calibration if b["lo"] == 0.5)
    assert math.isclose(float(bin5["frac_won"]), 0.5)


def test_empty_raises() -> None:
    with pytest.raises(ValueError):
        evaluate([])
