"""Entity paths (alfrd.entities): dict <-> URL form, validation, stable order."""

from __future__ import annotations

import pytest

from alfrd import entities as en


def test_round_trip_and_stable_order():
    e = {"line": 812, "file": "reductions/BV019/wd_1/casa.log", "step": "rpicard", "workdir": "wd_1",
         "project_code": "BV019", "target": "J0742+103", "project": "avica-t-0.3"}
    q = en.to_query(e)
    assert q.startswith("project=avica-t-0.3&target=J0742%2B103&project_code=BV019&workdir=wd_1&step=rpicard&file=")
    assert q.endswith("&line=812")
    back = en.from_query(q)
    assert back == en.make(e) and list(back) == ["project", "target", "project_code", "workdir", "step", "file", "line"]
    assert en.from_query("?" + q) == back and en.from_query(en.to_fragment(e)) == back
    # A literal "+" (hand-written link) is kept, not read as a space.
    assert en.from_query("project=p&target=J0742+103")["target"] == "J0742+103"


def test_unknown_level_and_bad_values_are_rejected():
    with pytest.raises(ValueError, match="unknown entity level"):
        en.make(project="p", galaxy="M87")
    with pytest.raises(ValueError, match="needs a project"):
        en.make(target="A")
    with pytest.raises(ValueError, match="integer"):
        en.make(project="p", file="a.log", line="x")
    with pytest.raises(ValueError, match="needs a file"):
        en.make(project="p", line=3)
    assert en.make(project="p", target="", step=None) == {"project": "p"}


def test_levels_come_from_the_template_hierarchy():
    manifest = {"hierarchy": [{"level": "night"}, {"level": "chip"}]}
    levels = en.levels_from(manifest)
    assert levels == ("target", "night", "chip")
    assert en.make(project="p", chip="3", night="n1", levels=levels) == {"project": "p", "night": "n1", "chip": "3"}
    with pytest.raises(ValueError):
        en.make(project="p", workdir="wd", levels=levels)
    assert en.levels_from({}) == en.AVICA_LEVELS
    assert en.contains({"project": "p", "target": "A"}, {"project": "p", "target": "A", "step": "s"})
