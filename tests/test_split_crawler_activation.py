from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_split_crawler_activation_is_gated_by_reviewed_batch() -> None:
    source = (
        ROOT / "deploy" / "ubuntu" / "activate_split_crawler.sh"
    ).read_text(encoding="utf-8")

    assert '--batch-id CRAWL_BATCH_ID' in source
    assert '!= "crawler"' in source
    assert 'mooncen-staging-apply-dry-run@${EXPECTED_BATCH_ID}.service' in source
    assert 'mooncen-staging-apply@${EXPECTED_BATCH_ID}.service' in source
    assert "run_pinned_staging_apply.py" in source
    assert "validate_staging_activation_result.py" in source
    assert "--dry-run-result-file \"$dry_run_result\"" in source
    assert 'systemctl clean --what=state "${timer_units[@]}"' in source
    assert 'systemctl enable "${timer_units[@]}"' in source
    assert 'systemctl start "${timer_units[@]}"' in source
    assert "systemctl enable --now mooncen-crawler.timer" not in source
    assert "trap rollback_timers EXIT" in source
    assert 'systemctl disable --now "${timer_units[@]}"' in source
    assert "systemctl disable --now mooncen-crawler.service" in source
    assert "systemctl disable --now mooncen-crawler.service >/dev/null 2>&1 || true" not in source
    assert "trap 'exit 130' INT" in source
    assert "trap 'exit 143' TERM" in source

    apply_validation = source.index("--mode apply")
    clean_state = source.index("systemctl clean --what=state")
    enable_timers = source.index('systemctl enable "${timer_units[@]}"')
    start_timers = source.index('systemctl start "${timer_units[@]}"')
    assert apply_validation < clean_state < enable_timers < start_timers


def test_activation_requires_protected_kakao_environment_and_seoul_timezone() -> None:
    source = (
        ROOT / "deploy" / "ubuntu" / "activate_split_crawler.sh"
    ).read_text(encoding="utf-8")

    assert "[/etc/mooncen/crawler.env]=mooncen-crawler" in source
    assert "[/etc/mooncen/applier.env]=mooncen-applier" in source
    assert "KAKAO_MAPS_REST_API_KEY=" in source
    assert "lacks a valid Kakao REST API key" in source
    assert "timedatectl show --property=Timezone --value" in source
    assert '!= "Asia/Seoul"' in source


def test_batch_specific_dry_run_uses_applier_only_environment() -> None:
    unit = (
        ROOT
        / "deploy"
        / "ubuntu"
        / "systemd"
        / "mooncen-staging-apply-dry-run@.service"
    ).read_text(encoding="utf-8")

    assert "User=mooncen-applier" in unit
    assert "EnvironmentFile=/etc/mooncen/applier.env" in unit
    assert "run_pinned_staging_dry_run.py --batch-id %i" in unit
    assert "--result-file /run/mooncen-staging-apply/dry-run-%i.json" in unit
    assert "StandardOutput=null" in unit
    assert "EnvironmentFile=/etc/mooncen/crawler.env" not in unit
    assert "CapabilityBoundingSet=\n" in unit
    activation = (
        ROOT / "deploy" / "ubuntu" / "activate_split_crawler.sh"
    ).read_text(encoding="utf-8")
    assert "mooncen-applier:mooncen-applier:600" in activation
    assert "root:root:600" in activation


def test_batch_specific_apply_is_pinned_and_sandboxed() -> None:
    unit = (
        ROOT
        / "deploy"
        / "ubuntu"
        / "systemd"
        / "mooncen-staging-apply@.service"
    ).read_text(encoding="utf-8")

    assert "User=mooncen-applier" in unit
    assert "EnvironmentFile=/etc/mooncen/applier.env" in unit
    assert "run_pinned_staging_apply.py --batch-id %i" in unit
    assert "--dry-run-result-file /run/mooncen-staging-apply/dry-run-%i.json" in unit
    assert "StandardOutput=file:/run/mooncen-staging-apply/apply-%i.json" in unit
    assert "EnvironmentFile=/etc/mooncen/crawler.env" not in unit
    assert "CapabilityBoundingSet=\n" in unit


def test_activation_timers_keep_later_catch_up_after_initial_state_cleanup() -> None:
    crawler_timer = (
        ROOT / "deploy" / "ubuntu" / "systemd" / "mooncen-crawler.timer"
    ).read_text(encoding="utf-8")
    apply_timer = (
        ROOT / "deploy" / "ubuntu" / "systemd" / "mooncen-staging-apply.timer"
    ).read_text(encoding="utf-8")

    assert "Persistent=true" in crawler_timer
    assert "Persistent=false" not in crawler_timer
    assert "Persistent=true" in apply_timer
    assert "Persistent=false" not in apply_timer


def test_expected_fingerprint_is_checked_before_primary_mutation() -> None:
    source = (ROOT / "tools" / "apply_staging_batch.py").read_text(
        encoding="utf-8"
    )

    selection = source.index("staging_fingerprint = staging_selection_fingerprint(")
    check = source.index("validate_expected_staging_fingerprint(", selection)
    primary_schema = source.index("ensure_primary_staging(primary_conn)", check)
    close_safety = source.index("if activation_full_apply and close_blocked:")
    upload = source.index("upload_snapshots(primary_conn", primary_schema)
    assert selection < check < primary_schema < close_safety < upload
    assert "selected_batch_id = latest_batch_id(staging_conn)" in source
    assert "pinned batch is no longer the latest eligible staging batch" in source
    assert "full-batch expected fingerprint requires --force" not in source
    assert "if not args.dry_run and not args.force" in source
