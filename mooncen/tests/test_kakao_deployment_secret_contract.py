from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_production_setup_requires_distinct_well_formed_kakao_keys() -> None:
    setup = (ROOT / "deploy" / "ubuntu" / "setup_project.sh").read_text(
        encoding="utf-8"
    )

    assert '[[ ! "$KAKAO_MAPS_JAVASCRIPT_KEY" =~ ^[0-9a-f]{32}$ ]]' in setup
    assert '[[ ! "$KAKAO_MAPS_REST_API_KEY" =~ ^[0-9a-f]{32}$ ]]' in setup
    assert '"$KAKAO_MAPS_JAVASCRIPT_KEY" = "$KAKAO_MAPS_REST_API_KEY"' in setup
    assert "Kakao JavaScript and REST API credentials must use their distinct app keys" in setup


def test_validated_kakao_keys_are_persisted_for_exact_remote_reuse() -> None:
    setup = (ROOT / "deploy" / "ubuntu" / "setup_project.sh").read_text(
        encoding="utf-8"
    )
    deploy = (
        ROOT / "deploy" / "ubuntu" / "deploy_from_windows.ps1"
    ).read_text(encoding="utf-8")

    persistence_block = setup.split(
        'deploy_secret_tmp="$(mktemp "$DEPLOY_SECRET_DIR/deploy-secrets.env.XXXXXX")"',
        1,
    )[1].split('} > "$deploy_secret_tmp"', 1)[0]
    expected = (
        ("KAKAO_MAPS_JAVASCRIPT_KEY", "KakaoMapsJavascriptKey"),
        ("KAKAO_MAPS_REST_API_KEY", "KakaoMapsRestApiKey"),
    )
    for env_name, parameter_name in expected:
        persist_line = f'write_deploy_secret_pair {env_name} "${env_name}"'
        reuse_line = f'${parameter_name} = Get-RemoteEnvValue "{env_name}"'
        assert persistence_block.count(persist_line) == 1
        assert deploy.count(reuse_line) == 1

    assert (
        '"$HOME/.config/mooncen/deploy-secrets.env" '
        '"$HOME/.config/mooncen/migrator.env"'
    ) in deploy


def test_google_maps_keys_are_removed_before_service_env_generation() -> None:
    setup = (ROOT / "deploy" / "ubuntu" / "setup_project.sh").read_text(
        encoding="utf-8"
    )

    start = setup.index("without_runtime_secrets()")
    end = setup.index("\n}\n", start)
    unset_block = setup[start:end]
    assert "GOOGLE_MAPS_API_KEY" in unset_block
    assert "VITE_GOOGLE_MAPS_API_KEY" in unset_block


def test_coordinate_service_runs_five_safe_kakao_passes_under_one_budget() -> None:
    unit = (
        ROOT / "deploy" / "ubuntu" / "systemd" / "mooncen-branch-coordinates.service"
    ).read_text(encoding="utf-8")
    runner = (
        ROOT / "deploy" / "ubuntu" / "mooncen_branch_coordinate_backfill.sh"
    ).read_text(encoding="utf-8")
    setup = (ROOT / "deploy" / "ubuntu" / "setup_project.sh").read_text(
        encoding="utf-8"
    )

    assert unit.count("ExecStart=") == 1
    assert "mooncen_branch_coordinate_backfill.sh" in unit
    assert "SuccessExitStatus=3" in unit
    assert "set -Eeuo pipefail" in runner
    assert '"$PYTHON_BIN" -X utf8 "$VERIFIED_COPY"' in runner
    assert "--with-active-courses" in runner
    assert "VERIFIED_SAME_NAME_COPY" not in runner
    assert "run_pass \"$address_budget\" 30 --address-only" in runner
    assert "run_pass \"$course_address_budget\" 30 --course-address-only" in runner
    assert "run_pass \"$stored_region_budget\" 14 --region-keyword-only" in runner
    assert "run_pass \"$configured_locality_budget\" 30 --configured-locality-only" in runner
    assert (
        "run_pass \"$legacy_reverify_budget\" 30 --verify-existing "
        "--coordinate-source-prefix GOOGLE"
    ) in runner
    assert runner.count("--max-requests") == 1
    assert "legacy_reverify_budget=$((" in runner
    assert "total_budget" in runner
    assert "if (( exit_code == 3 )); then" in runner
    assert "partial_progress=1" in runner
    assert "if (( partial_progress != 0 )); then" in runner
    assert "CRAWLER_COORDINATE_BACKFILL_LIMIT=100" in setup
