from tools.maintenance.audit_emart_branch_locations import score_candidate


def test_score_candidate_rejects_other_emart_formats():
    assert (
        score_candidate(
            "스타필드안성점",
            "이마트24 스타필드안성",
            "경기도 안성시 공도읍 진사리",
            "places",
        )
        == 0
    )
    assert (
        score_candidate(
            "스타필드시티부천점",
            "이마트 중동점",
            "경기도 부천시 원미구 석천로 188",
            "places",
        )
        == 0
    )


def test_score_candidate_keeps_matching_special_store_formats():
    assert (
        score_candidate(
            "스타필드시티위례점",
            "스타필드 시티 위례",
            "경기도 하남시 위례대로 200",
            "places",
        )
        >= 82
    )
    assert (
        score_candidate(
            "트레이더스연산점",
            "컬처클럽 트레이더스 연산점",
            "부산광역시 연제구 좌수영로 241",
            "places",
        )
        >= 82
    )
