from service_group import (
    SERVICE_GROUP_CULTURE_CENTER,
    SERVICE_GROUP_EXPERIENCE,
    SERVICE_GROUP_OTHER,
    SERVICE_GROUP_PUBLIC_COURSE,
    infer_experience_institution_source_group,
    infer_service_group,
)


def test_culture_center_provider_wins():
    assert infer_service_group(provider="HOMEPLUS", domain_category="art") == SERVICE_GROUP_CULTURE_CENTER


def test_experience_like_sources_are_experience():
    assert infer_service_group(source_group="museum_science") == SERVICE_GROUP_EXPERIENCE
    assert infer_service_group(source_group="arts_culture") == SERVICE_GROUP_EXPERIENCE


def test_public_course_sources_are_grouped_together():
    assert infer_service_group(source_group="lifelong_learning") == SERVICE_GROUP_PUBLIC_COURSE
    assert infer_service_group(provider="MUNI_WWW_GURO_GO_KR_A4A5D3E3") == SERVICE_GROUP_PUBLIC_COURSE
    assert infer_service_group(source_group="municipal_reservation") == SERVICE_GROUP_PUBLIC_COURSE


def test_unknown_group_falls_back_to_other():
    assert infer_service_group(provider="UNKNOWN_PROVIDER") == SERVICE_GROUP_OTHER


def test_explicit_aliases_use_the_authoritative_normalization_contract():
    assert infer_service_group(service_group="교육") == SERVICE_GROUP_PUBLIC_COURSE
    assert infer_service_group(service_group="체험 견학") == SERVICE_GROUP_EXPERIENCE


def test_row_level_experience_evidence_overrides_a_public_target_default():
    assert (
        infer_service_group(
            provider="MUNI_EXAMPLE",
            source_group="public_reservation",
            domain_category="공공예약",
            category_raw="문화/체험",
            program_type="체험",
            service_group="공공강좌",
        )
        == SERVICE_GROUP_EXPERIENCE
    )


def test_library_sources_default_to_public_education():
    assert (
        infer_service_group(
            provider="MUNI_LIBRARY",
            source_group="library",
            branch_name="향남복합문화센터도서관",
        )
        == SERVICE_GROUP_PUBLIC_COURSE
    )


def test_explicit_library_experience_stays_in_experience():
    assert (
        infer_service_group(
            provider="MUNI_LIBRARY",
            source_group="library",
            branch_name="향남복합문화센터도서관",
            title="어린이 천문 체험",
            program_type="체험",
            service_group="공공강좌",
        )
        == SERVICE_GROUP_EXPERIENCE
    )


def test_an_incidental_library_word_does_not_turn_a_public_course_into_experience():
    assert (
        infer_service_group(
            provider="MUNI_LIFELONG",
            source_group="lifelong_learning",
            domain_category="평생학습",
            branch_name="월담작은도서관",
            title="정리수납 배우기",
            service_group="공공강좌",
        )
        == SERVICE_GROUP_PUBLIC_COURSE
    )


def test_institution_target_metadata_identifies_library_and_museum_sources():
    assert (
        infer_experience_institution_source_group(
            source_group="municipal_reservation",
            name="강동구립도서관 전체 교육·독서 프로그램",
            domain_category="교육·강좌",
        )
        == "library"
    )
    assert (
        infer_experience_institution_source_group(
            source_group="public_reservation",
            name="남원시립김병종미술관 견학",
        )
        == "museum_science"
    )
    assert not infer_experience_institution_source_group(
        source_group="lifelong_learning",
        name="정리수납 배우기",
        branch_name="월담마을",
    )


def test_non_program_reservations_are_not_promoted_by_a_facility_name():
    assert (
        infer_service_group(
            provider="MUNI_RESERVATION",
            source_group="public_reservation",
            category_raw="백두대간생태체험교육장 / 캠핑장",
            program_type="숙박",
            service_group="체험",
        )
        == SERVICE_GROUP_PUBLIC_COURSE
    )


def test_deprecated_sources_remain_excluded_even_with_experience_words():
    assert (
        infer_service_group(
            provider="NATIONAL_ECOLOGY_CENTER",
            source_group="deprecated",
            domain_category="제외",
            title="일일생태체험",
            program_type="체험",
            service_group="체험",
        )
        == SERVICE_GROUP_OTHER
    )


def test_generic_event_program_type_is_not_sufficient_experience_evidence():
    assert (
        infer_service_group(
            provider="MUNI_PUBLIC",
            source_group="sports_facility",
            title="플로리스트 자격 취득반",
            category_raw="문화 > 문화",
            program_type="행사",
            service_group="공공강좌",
        )
        == SERVICE_GROUP_PUBLIC_COURSE
    )
