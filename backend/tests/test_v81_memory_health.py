"""Memory health engine: evidence-based findings, non-destructive remedies."""

from app.cognition.health import (Finding, MemoryHealthEngine, REMEDIES,
                                  _similarity)


def engine(runtime):
    return runtime.cognition.health


def test_similarity_separates_duplicates_from_distinct_memories():
    """Filler words and plurals must not hide real duplicates, and near-miss
    pairs (including negations) must not be merged."""
    duplicates = [
        ("Prefers Python for backend work", "Prefers Python for the backend work"),
        ("Deploys with GitHub Actions every Friday",
         "Deploys using GitHub Actions each Friday"),
        ("Prefers dark mode in every editor", "Prefers dark mode in all editors"),
    ]
    distinct = [
        ("Prefers Python for backend work", "Lives in Berlin with a cat"),
        ("Deploys on Friday", "Never deploys on Friday"),
        ("Uses Postgres for analytics", "Uses Postgres for billing"),
    ]
    for a, b in duplicates:
        assert _similarity(a, b) >= 0.82, (a, b)
    for a, b in distinct:
        assert _similarity(a, b) < 0.82, (a, b)


def test_duplicate_is_detected_with_cited_evidence(runtime):
    user = "health-dup"
    runtime.memory.create(user, "Prefers Python for backend work",
                          category="PREFERENCE", allow_duplicate=True)
    runtime.memory.create(user, "Prefers Python for the backend work",
                          category="PREFERENCE", allow_duplicate=True)
    findings = [f for f in engine(runtime).diagnose(user) if f.issue == "duplicate"]
    assert findings
    assert findings[0].remedy == "MERGE"
    assert "overlap" in findings[0].evidence
    assert findings[0].related_id


def test_low_value_memory_is_downgraded_not_deleted(runtime):
    user = "health-low"
    created = runtime.memory.create(user, "Mentioned liking blue once",
                                    category="PERSONAL", importance=0.15,
                                    allow_duplicate=True)
    memory_id = created["memory"]["id"]
    finding = next(f for f in engine(runtime).diagnose(user)
                   if f.memory_id == memory_id and f.issue == "low_value")
    assert finding.remedy == "DOWNGRADE"

    before = runtime.memory.get(memory_id).importance
    engine(runtime).apply(user, finding)
    after = runtime.memory.get(memory_id)
    assert after is not None                  # still exists
    assert after.status == "active"           # still usable
    assert after.importance < before          # but weighted lower


def test_merge_retires_duplicate_but_preserves_the_row(runtime):
    user = "health-merge"
    runtime.memory.create(user, "Deploys with GitHub Actions every Friday",
                          category="HABIT", allow_duplicate=True)
    runtime.memory.create(user, "Deploys using GitHub Actions each Friday",
                          category="HABIT", allow_duplicate=True)
    finding = next(f for f in engine(runtime).diagnose(user) if f.issue == "duplicate")
    result = engine(runtime).apply(user, finding)

    assert result["applied"] is True
    retired = runtime.memory.get(finding.memory_id)
    assert retired is not None                # history is never destroyed
    assert retired.status == "retired"
    assert runtime.memory.get(finding.related_id).status == "active"


def test_health_report_converges_after_remedies_are_applied(runtime):
    """Applying remedies must settle, not re-flag the same memories forever."""
    user = "health-converge"
    runtime.memory.create(user, "Prefers dark mode in every editor",
                          category="PREFERENCE", allow_duplicate=True)
    runtime.memory.create(user, "Prefers dark mode in all editors",
                          category="PREFERENCE", allow_duplicate=True)
    health = engine(runtime)
    for finding in health.diagnose(user):
        health.apply(user, finding)
    for finding in health.diagnose(user):
        health.apply(user, finding)
    assert health.diagnose(user) == []


def test_clean_store_is_graded_healthy(runtime):
    user = "health-clean"
    runtime.memory.create(user, "Works in Berlin as a staff engineer",
                          category="IDENTITY", allow_duplicate=True)
    report = health_report = engine(runtime).report(user)
    assert report["grade"] == "HEALTHY"
    assert health_report["findings"] == []


def test_empty_store_reports_insufficient_evidence(runtime):
    assert engine(runtime).report("health-empty")["grade"] == "INSUFFICIENT EVIDENCE"


def test_every_remedy_is_in_the_declared_vocabulary(runtime):
    user = "health-vocab"
    runtime.memory.create(user, "Uses pytest for all backend testing",
                          category="HABIT", importance=0.2, allow_duplicate=True)
    assert all(f.remedy in REMEDIES for f in engine(runtime).diagnose(user))


def test_applying_a_finding_for_a_missing_memory_is_safe(runtime):
    result = engine(runtime).apply("health-missing", Finding(
        memory_id="mem_does_not_exist", issue="stale", remedy="DOWNGRADE",
        confidence=0.5, evidence="n/a"))
    assert result["applied"] is False
