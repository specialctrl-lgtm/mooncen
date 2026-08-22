from __future__ import annotations

import pytest

from Crawler import municipal_andong
from Crawler import municipal_ansan
from Crawler import municipal_boeun
from Crawler import municipal_bonghwa
from Crawler import municipal_boryeong
from Crawler import municipal_buan
from Crawler import municipal_buyeo
from Crawler import municipal_busan_junggu
from Crawler import municipal_busan_bukgu
from Crawler import municipal_busan_busanjin
from Crawler import municipal_busan_gangseo
from Crawler import municipal_busan_gijang
from Crawler import municipal_busan_haeundae
from Crawler import municipal_busan_dongnae
from Crawler import municipal_busan_namgu
from Crawler import municipal_busan_saha
from Crawler import municipal_busan_sasang
from Crawler import municipal_busan_seogu
from Crawler import municipal_busan_suyeong
from Crawler import municipal_busan_yeonje
from Crawler import municipal_busan_yeongdo
from Crawler import municipal_cheonan
from Crawler import municipal_cheongyang
from Crawler import municipal_cheongsong
from Crawler import municipal_cheongdo
from Crawler import municipal_chilgok
from Crawler import municipal_chuncheon
from Crawler import municipal_daegu_gunwi
from Crawler import municipal_daegu_namgu
from Crawler import municipal_daegu_suseong
from Crawler import municipal_hampyeong
from Crawler import municipal_hapcheon
from Crawler import municipal_hongcheon
from Crawler import municipal_hongseong
from Crawler import municipal_hoengseong
from Crawler import municipal_gwangju_bukgu_library
from Crawler import municipal_gwangmyeong
from Crawler import municipal_goryeong
from Crawler import municipal_geoje
from Crawler import municipal_geochang
from Crawler import municipal_gimhae
from Crawler import municipal_gimcheon
from Crawler import municipal_gyeongnam_changnyeong
from Crawler import municipal_gyeongsan
from Crawler import municipal_gyeongju
from Crawler import municipal_gwacheon
from Crawler import municipal_gyeryong
from Crawler import municipal_damyang
from Crawler import municipal_hwasun
from Crawler import municipal_incheon_ganghwa
from Crawler import municipal_incheon_ongjin
from Crawler import municipal_incheon_gyeyang
from Crawler import municipal_incheon_yeonsu
from Crawler import municipal_iksan
from Crawler import municipal_imsil
from Crawler import municipal_gurye
from Crawler import municipal_gunpo
from Crawler import municipal_gyeonggi_gwangju
from Crawler import municipal_yangpyeong
from Crawler import municipal_yongin
from Crawler import municipal_haenam
from Crawler import municipal_haman
from Crawler import municipal_hanam
from Crawler import municipal_icheon
from Crawler import municipal_gochang
from Crawler import municipal_goheung
from Crawler import municipal_jangseong
from Crawler import municipal_jindo
from Crawler import municipal_muan
from Crawler import municipal_muju
from Crawler import municipal_namyangju
from Crawler import municipal_samcheok
from Crawler import municipal_shinan
from Crawler import municipal_sangju
from Crawler import municipal_seocheon
from Crawler import municipal_seosan
from Crawler import municipal_siheung
from Crawler import municipal_siheung_sports
from Crawler import municipal_sunchang
from Crawler import municipal_taebaek
from Crawler import municipal_yeonggwang
from Crawler import municipal_yeongam
from Crawler import municipal_yeongdeok
from Crawler import municipal_yeongyang
from Crawler import municipal_jincheon
from Crawler import municipal_jinan
from Crawler import municipal_jangsu
from Crawler import municipal_jeongeup
from Crawler import municipal_jeju_city
from Crawler import municipal_jeungpyeong
from Crawler import municipal_jeongseon
from Crawler import municipal_jeongseon_library
from Crawler import municipal_jeongseon_municipal_library
from Crawler import municipal_yeongdong
from Crawler import municipal_yeongju
from Crawler import municipal_yeoju
from Crawler import municipal_donghae
from Crawler import municipal_dongducheon
from Crawler import municipal_goesan
from Crawler import municipal_geumsan
from Crawler import municipal_gumi
from Crawler import municipal_gapyeong
from Crawler import municipal_okcheon
from Crawler import municipal_nonsan
from Crawler import municipal_namdong
from Crawler import municipal_tongyeong
from Crawler import municipal_taean
from Crawler import municipal_uiseong
from Crawler import municipal_uljin
from Crawler import municipal_ulleung
from Crawler import municipal_wanju
from Crawler import municipal_wonju
from Crawler import municipal_osan
from Crawler import municipal_paju
from Crawler import municipal_seongju
from Crawler import municipal_yangsan


@pytest.mark.parametrize(
    ("module", "collector_name", "provider", "url"),
    (
        (
            municipal_ansan,
            "collect_ansan_education_courses",
            municipal_ansan.ANSAN_PROVIDER,
            municipal_ansan.ANSAN_CANONICAL_URL,
        ),
        (
            municipal_chuncheon,
            "collect_chuncheon_education",
            municipal_chuncheon.CHUNCHEON_PROVIDER,
            municipal_chuncheon.CHUNCHEON_CANONICAL_URL,
        ),
        (
            municipal_jeongseon,
            "collect_jeongseon_education",
            municipal_jeongseon.JEONGSEON_PROVIDER,
            "https://edu.jeongseon.go.kr/lecture?quarter=LifeEdu",
        ),
        (
            municipal_jeongseon_library,
            "collect_jeongseon_library",
            municipal_jeongseon_library.JEONGSEON_LIBRARY_PROVIDER,
            municipal_jeongseon_library.JEONGSEON_LIBRARY_LIST_URL,
        ),
        (
            municipal_jeongseon_municipal_library,
            "collect_jeongseon_municipal_library",
            municipal_jeongseon_municipal_library.JEONGSEON_MUNICIPAL_LIBRARY_PROVIDER,
            municipal_jeongseon_municipal_library.JEONGSEON_MUNICIPAL_LIBRARY_URL,
        ),
        (
            municipal_muan,
            "collect_muan_education",
            municipal_muan.MUAN_PROVIDER,
            municipal_muan.MUAN_LIFELONG_URL,
        ),
        (
            municipal_hampyeong,
            "collect_hampyeong_education_courses",
            municipal_hampyeong.HAMPYEONG_PROVIDER,
            municipal_hampyeong.HAMPYEONG_CANONICAL_URL,
        ),
        (
            municipal_jangseong,
            "collect_jangseong_education",
            municipal_jangseong.JANGSEONG_PROVIDER,
            municipal_jangseong.JANGSEONG_CANONICAL_URL,
        ),
        (
            municipal_yeonggwang,
            "collect_yeonggwang_education_courses",
            municipal_yeonggwang.YEONGGWANG_PROVIDER,
            municipal_yeonggwang.YEONGGWANG_CANONICAL_URL,
        ),
        (
            municipal_jindo,
            "collect_jindo_education",
            municipal_jindo.JINDO_PROVIDER,
            municipal_jindo.JINDO_URL,
        ),
        (
            municipal_shinan,
            "collect_shinan_education",
            municipal_shinan.SHINAN_FAMILY_PROVIDER,
            municipal_shinan.SHINAN_FAMILY_LIST_URL,
        ),
        (
            municipal_gwangju_bukgu_library,
            "collect_gwangju_bukgu_library_education",
            municipal_gwangju_bukgu_library.GWANGJU_BUKGU_LIBRARY_PROVIDER,
            municipal_gwangju_bukgu_library.GWANGJU_BUKGU_LIBRARY_CANONICAL_URL,
        ),
        (
            municipal_damyang,
            "collect_damyang_education",
            municipal_damyang.DAMYANG_LIBRARY_PROVIDER,
            municipal_damyang.DAMYANG_LIBRARY_URL,
        ),
        (
            municipal_damyang,
            "collect_damyang_education",
            municipal_damyang.DAMYANG_LIFELONG_PROVIDER,
            municipal_damyang.DAMYANG_LIFELONG_URL,
        ),
        (
            municipal_hwasun,
            "collect_hwasun_education",
            municipal_hwasun.HWASUN_PROVIDER,
            municipal_hwasun.HWASUN_CANONICAL_URL,
        ),
        (
            municipal_gurye,
            "collect_gurye_education",
            municipal_gurye.GURYE_PROVIDER,
            municipal_gurye.GURYE_LIST_URL,
        ),
        (
            municipal_yeongam,
            "collect_yeongam_education",
            municipal_yeongam.YEONGAM_PROVIDER,
            municipal_yeongam.YEONGAM_CANONICAL_URL,
        ),
        (
            municipal_haenam,
            "collect_haenam_education",
            municipal_haenam.HAENAM_FOUNDATION_PROVIDER,
            municipal_haenam.HAENAM_FOUNDATION_URL,
        ),
        (
            municipal_haenam,
            "collect_haenam_education",
            municipal_haenam.HAENAM_LIBRARY_PROVIDER,
            municipal_haenam.HAENAM_LIBRARY_URL,
        ),
        (
            municipal_goheung,
            "collect_goheung_education",
            municipal_goheung.GOHEUNG_COUNTY_PROVIDER,
            municipal_goheung.GOHEUNG_COUNTY_URL,
        ),
        (
            municipal_goheung,
            "collect_goheung_education",
            municipal_goheung.GOHEUNG_LIFELONG_PROVIDER,
            municipal_goheung.GOHEUNG_LIFELONG_URL,
        ),
        (
            municipal_goheung,
            "collect_goheung_education",
            municipal_goheung.GOHEUNG_LIBRARY_PROVIDER,
            municipal_goheung.GOHEUNG_LIBRARY_URL,
        ),
        (
            municipal_jincheon,
            "collect_jincheon_education",
            municipal_jincheon.JINCHEON_PROVIDER,
            municipal_jincheon.JINCHEON_CANONICAL_URL,
        ),
        (
            municipal_yeongju,
            "collect_yeongju_education",
            municipal_yeongju.YEONGJU_PROVIDER,
            municipal_yeongju.YEONGJU_CANONICAL_URL,
        ),
        (
            municipal_donghae,
            "collect_donghae_education",
            municipal_donghae.DONGHAE_PROVIDER,
            municipal_donghae.DONGHAE_CANONICAL_URL,
        ),
        (
            municipal_goesan,
            "collect_goesan_education",
            municipal_goesan.GOESAN_PROVIDER,
            municipal_goesan.GOESAN_CANONICAL_URL,
        ),
        (
            municipal_gochang,
            "collect_gochang_education",
            municipal_gochang.GOCHANG_PROVIDER,
            municipal_gochang.GOCHANG_CANONICAL_URL,
        ),
        (
            municipal_geumsan,
            "collect_geumsan_education",
            municipal_geumsan.GEUMSAN_PROVIDER,
            municipal_geumsan.GEUMSAN_CANONICAL_URL,
        ),
        (
            municipal_gumi,
            "collect_gumi_education",
            municipal_gumi.GUMI_PROVIDER,
            municipal_gumi.GUMI_CANONICAL_URL,
        ),
        (
            municipal_busan_junggu,
            "collect_busan_junggu_education",
            municipal_busan_junggu.BUSAN_JUNGGU_PROVIDER,
            municipal_busan_junggu.BUSAN_JUNGGU_CANONICAL_URL,
        ),
        (
            municipal_busan_bukgu,
            "collect_busan_bukgu_education",
            municipal_busan_bukgu.BUSAN_BUKGU_PROVIDER,
            municipal_busan_bukgu.BUSAN_BUKGU_CANONICAL_URL,
        ),
        (
            municipal_busan_busanjin,
            "collect_busan_busanjin_education",
            municipal_busan_busanjin.BUSAN_BUSANJIN_PROVIDER,
            municipal_busan_busanjin.BUSAN_BUSANJIN_REGISTERED_URL,
        ),
        (
            municipal_busan_gangseo,
            "collect_busan_gangseo_education",
            municipal_busan_gangseo.BUSAN_GANGSEO_PROVIDER,
            municipal_busan_gangseo.BUSAN_GANGSEO_CANONICAL_URL,
        ),
        (
            municipal_busan_saha,
            "collect_busan_saha_education",
            municipal_busan_saha.BUSAN_SAHA_PROVIDER,
            municipal_busan_saha.BUSAN_SAHA_CANONICAL_URL,
        ),
        (
            municipal_busan_haeundae,
            "collect_busan_haeundae_education",
            municipal_busan_haeundae.BUSAN_HAEUNDAE_PROVIDER,
            municipal_busan_haeundae.BUSAN_HAEUNDAE_CANONICAL_URL,
        ),
        (
            municipal_busan_gijang,
            "collect_busan_gijang_education",
            municipal_busan_gijang.BUSAN_GIJANG_PROVIDER,
            municipal_busan_gijang.BUSAN_GIJANG_CANONICAL_URL,
        ),
        (
            municipal_busan_sasang,
            "collect_busan_sasang_education",
            municipal_busan_sasang.BUSAN_SASANG_PROVIDER,
            municipal_busan_sasang.BUSAN_SASANG_CANONICAL_URL,
        ),
        (
            municipal_busan_yeonje,
            "collect_busan_yeonje_education",
            municipal_busan_yeonje.BUSAN_YEONJE_PROVIDER,
            municipal_busan_yeonje.BUSAN_YEONJE_CANONICAL_URL,
        ),
        (
            municipal_busan_suyeong,
            "collect_busan_suyeong_education",
            municipal_busan_suyeong.BUSAN_SUYEONG_PROVIDER,
            municipal_busan_suyeong.BUSAN_SUYEONG_CANONICAL_URL,
        ),
        (
            municipal_busan_seogu,
            "collect_busan_seogu_education",
            municipal_busan_seogu.BUSAN_SEOGU_PROVIDER,
            municipal_busan_seogu.BUSAN_SEOGU_URL,
        ),
        (
            municipal_daegu_gunwi,
            "collect_daegu_gunwi_education",
            municipal_daegu_gunwi.DAEGU_GUNWI_PROVIDER,
            municipal_daegu_gunwi.DAEGU_GUNWI_CANONICAL_URL,
        ),
        (
            municipal_daegu_namgu,
            "collect_daegu_namgu_education",
            municipal_daegu_namgu.DAEGU_NAMGU_PROVIDER,
            municipal_daegu_namgu.DAEGU_NAMGU_URL,
        ),
        (
            municipal_daegu_suseong,
            "collect_daegu_suseong_education",
            municipal_daegu_suseong.DAEGU_SUSEONG_PROVIDER,
            municipal_daegu_suseong.DAEGU_SUSEONG_URL,
        ),
        (
            municipal_busan_dongnae,
            "collect_busan_dongnae_education",
            municipal_busan_dongnae.BUSAN_DONGNAE_PROVIDER,
            municipal_busan_dongnae.BUSAN_DONGNAE_CANONICAL_URL,
        ),
        (
            municipal_busan_namgu,
            "collect_busan_namgu_education",
            municipal_busan_namgu.BUSAN_NAMGU_PROVIDER,
            municipal_busan_namgu.BUSAN_NAMGU_REGISTERED_URL,
        ),
        (
            municipal_busan_yeongdo,
            "collect_busan_yeongdo_education",
            municipal_busan_yeongdo.BUSAN_YEONGDO_PROVIDER,
            municipal_busan_yeongdo.BUSAN_YEONGDO_CANONICAL_URL,
        ),
        (
            municipal_andong,
            "collect_andong_education",
            municipal_andong.ANDONG_PROVIDER,
            municipal_andong.ANDONG_CANONICAL_URL,
        ),
        (
            municipal_andong,
            "collect_andong_education",
            municipal_andong.ANDONG_LIBRARY_CULTURE_PROVIDER,
            municipal_andong.ANDONG_LIBRARY_CULTURE_URL,
        ),
        (
            municipal_andong,
            "collect_andong_education",
            municipal_andong.ANDONG_LIBRARY_EVENT_PROVIDER,
            municipal_andong.ANDONG_LIBRARY_EVENT_URL,
        ),
        (
            municipal_taean,
            "collect_taean_education",
            municipal_taean.TAEAN_PROVIDER,
            municipal_taean.TAEAN_CANONICAL_URL,
        ),
        (
            municipal_gwangmyeong,
            "collect_gwangmyeong_education_courses",
            municipal_gwangmyeong.GWANGMYEONG_PROVIDER,
            municipal_gwangmyeong.GWANGMYEONG_CANONICAL_URL,
        ),
        (
            municipal_iksan,
            "collect_iksan_education",
            municipal_iksan.IKSAN_PROVIDER,
            municipal_iksan.IKSAN_CANONICAL_URL,
        ),
        (
            municipal_jeungpyeong,
            "collect_jeungpyeong_education",
            municipal_jeungpyeong.JEUNGPYEONG_LIFELONG_PROVIDER,
            municipal_jeungpyeong.JEUNGPYEONG_LIFELONG_CANONICAL_URL,
        ),
        (
            municipal_jeungpyeong,
            "collect_jeungpyeong_education",
            municipal_jeungpyeong.JEUNGPYEONG_LIBRARY_PROVIDER,
            municipal_jeungpyeong.JEUNGPYEONG_LIBRARY_CANONICAL_URL,
        ),
        (
            municipal_yeongdong,
            "collect_yeongdong_education",
            municipal_yeongdong.YEONGDONG_COUNTY_PROVIDER,
            municipal_yeongdong.YEONGDONG_COUNTY_URL,
        ),
        (
            municipal_yeongdong,
            "collect_yeongdong_education",
            municipal_yeongdong.YEONGDONG_LIBRARY_PROVIDER,
            municipal_yeongdong.YEONGDONG_LIBRARY_URL,
        ),
        (
            municipal_boryeong,
            "collect_boryeong_education",
            municipal_boryeong.BORYEONG_PROVIDER,
            municipal_boryeong.BORYEONG_CANONICAL_URL,
        ),
        (
            municipal_gapyeong,
            "collect_gapyeong_education",
            municipal_gapyeong.GAPYEONG_PROVIDER,
            municipal_gapyeong.GAPYEONG_CANONICAL_URL,
        ),
        (
            municipal_boeun,
            "collect_boeun_education",
            municipal_boeun.BOEUN_PROVIDER,
            municipal_boeun.BOEUN_CANONICAL_URL,
        ),
        (
            municipal_okcheon,
            "collect_okcheon_education",
            municipal_okcheon.OKCHEON_PROVIDER,
            municipal_okcheon.OKCHEON_CANONICAL_URL,
        ),
        (
            municipal_tongyeong,
            "collect_tongyeong_education",
            municipal_tongyeong.TONGYEONG_GNE_PROVIDER,
            municipal_tongyeong.TONGYEONG_GNE_LIST_URL,
        ),
        (
            municipal_tongyeong,
            "collect_tongyeong_education",
            municipal_tongyeong.TONGYEONG_CITY_PROVIDER,
            municipal_tongyeong.TONGYEONG_CITY_LIST_URL,
        ),
        (
            municipal_tongyeong,
            "collect_tongyeong_education",
            municipal_tongyeong.TONGYEONG_LIBRARY_PROVIDER,
            municipal_tongyeong.TONGYEONG_LIBRARY_LIST_URL,
        ),
        (
            municipal_dongducheon,
            "collect_dongducheon_education_courses",
            municipal_dongducheon.DONGDUCHEON_PROVIDER,
            municipal_dongducheon.DONGDUCHEON_URL,
        ),
        (
            municipal_cheongyang,
            "collect_cheongyang_education",
            municipal_cheongyang.CHEONGYANG_PROVIDER,
            municipal_cheongyang.CHEONGYANG_CANONICAL_URL,
        ),
        (
            municipal_gyeongnam_changnyeong,
            "collect_gyeongnam_changnyeong_education_courses",
            municipal_gyeongnam_changnyeong.CHANGNYEONG_PROVIDER,
            municipal_gyeongnam_changnyeong.CHANGNYEONG_URL,
        ),
        (
            municipal_geoje,
            "collect_geoje_lifelong_education_courses",
            municipal_geoje.GEOJE_LIFELONG_PROVIDER,
            municipal_geoje.GEOJE_LIFELONG_URL,
        ),
        (
            municipal_yeoju,
            "collect_yeoju_education_courses",
            municipal_yeoju.YEOJU_PROVIDER,
            municipal_yeoju.YEOJU_URL,
        ),
        (
            municipal_siheung,
            "collect_siheung_education_courses",
            municipal_siheung.SIHEUNG_PROVIDER,
            municipal_siheung.SIHEUNG_URL,
        ),
        (
            municipal_siheung_sports,
            "collect_siheung_sports_courses",
            municipal_siheung_sports.SIHEUNG_SPORTS_PROVIDER,
            municipal_siheung_sports.SIHEUNG_SPORTS_URL,
        ),
        (
            municipal_hanam,
            "collect_hanam_education_courses",
            municipal_hanam.HANAM_GSEEK_PROVIDER,
            municipal_hanam.HANAM_GSEEK_URL,
        ),
        (
            municipal_hanam,
            "collect_hanam_education_courses",
            municipal_hanam.HANAM_RESIDENT_PROVIDER,
            municipal_hanam.HANAM_RESIDENT_URL,
        ),
        (
            municipal_hanam,
            "collect_hanam_education_courses",
            municipal_hanam.HANAM_YOUTH_PROVIDER,
            municipal_hanam.HANAM_YOUTH_URL,
        ),
        (
            municipal_hanam,
            "collect_hanam_education_courses",
            municipal_hanam.HANAM_HDREAM_PROVIDER,
            municipal_hanam.HANAM_HDREAM_URL,
        ),
        (
            municipal_hanam,
            "collect_hanam_education_courses",
            municipal_hanam.HANAM_LIBRARY_PROVIDER,
            municipal_hanam.HANAM_LIBRARY_URL,
        ),
        *(
            (
                municipal_gunpo,
                "collect_gunpo_education_courses",
                config["provider"],
                config["url"],
            )
            for config in municipal_gunpo.GUNPO_OWNERS.values()
        ),
        (
            municipal_gyeonggi_gwangju,
            "collect_gyeonggi_gwangju_education_courses",
            municipal_gyeonggi_gwangju.GYEONGGI_GWANGJU_GSEEK_PROVIDER,
            municipal_gyeonggi_gwangju.GYEONGGI_GWANGJU_GSEEK_URL,
        ),
        (
            municipal_gyeonggi_gwangju,
            "collect_gyeonggi_gwangju_education_courses",
            municipal_gyeonggi_gwangju.GYEONGGI_GWANGJU_RESIDENT_PROVIDER,
            municipal_gyeonggi_gwangju.GYEONGGI_GWANGJU_RESIDENT_URL,
        ),
        (
            municipal_gyeonggi_gwangju,
            "collect_gyeonggi_gwangju_education_courses",
            municipal_gyeonggi_gwangju.GYEONGGI_GWANGJU_LIBRARY_PROVIDER,
            municipal_gyeonggi_gwangju.GYEONGGI_GWANGJU_LIBRARY_URL,
        ),
        (
            municipal_gyeonggi_gwangju,
            "collect_gyeonggi_gwangju_education_courses",
            municipal_gyeonggi_gwangju.GYEONGGI_GWANGJU_IT_PROVIDER,
            municipal_gyeonggi_gwangju.GYEONGGI_GWANGJU_IT_URL,
        ),
        (
            municipal_gyeonggi_gwangju,
            "collect_gyeonggi_gwangju_education_courses",
            municipal_gyeonggi_gwangju.GYEONGGI_GWANGJU_AGRI_PROVIDER,
            municipal_gyeonggi_gwangju.GYEONGGI_GWANGJU_AGRI_URL,
        ),
        (
            municipal_gyeonggi_gwangju,
            "collect_gyeonggi_gwangju_education_courses",
            municipal_gyeonggi_gwangju.GYEONGGI_GWANGJU_YOUTH_PROVIDER,
            municipal_gyeonggi_gwangju.GYEONGGI_GWANGJU_YOUTH_URL,
        ),
        *(
            (
                municipal_yangpyeong,
                "collect_yangpyeong_education_courses",
                config["provider"],
                config["url"],
            )
            for config in municipal_yangpyeong.YANGPYEONG_OWNERS.values()
        ),
        *(
            (
                municipal_yongin,
                "collect_yongin_education_courses",
                provider,
                url,
            )
            for provider, url in municipal_yongin.YONGIN_EXECUTING_TARGETS
        ),
        *(
            (
                municipal_cheonan,
                "collect_cheonan_education_courses",
                provider,
                url,
            )
            for provider, url in municipal_cheonan.CHEONAN_EXECUTING_TARGETS
        ),
        *(
            (
                municipal_jeju_city,
                "collect_jeju_city_education_courses",
                provider,
                url,
            )
            for provider, url in municipal_jeju_city.JEJU_EXECUTING_TARGETS
        ),
        *(
            (
                municipal_samcheok,
                "collect_samcheok_education",
                config["provider"],
                config["url"],
            )
            for config in municipal_samcheok.SAMCHEOK_OWNERS.values()
        ),
        *(
            (
                municipal_hoengseong,
                "collect_hoengseong_education",
                config["provider"],
                config["url"],
            )
            for config in municipal_hoengseong.HOENGSEONG_OWNERS.values()
        ),
        (
            municipal_icheon,
            "collect_icheon_education_courses",
            municipal_icheon.ICHEON_CITY_PROVIDER,
            municipal_icheon.ICHEON_CITY_URL,
        ),
        (
            municipal_icheon,
            "collect_icheon_education_courses",
            municipal_icheon.ICHEON_GSEEK_PROVIDER,
            municipal_icheon.ICHEON_GSEEK_URL,
        ),
        (
            municipal_icheon,
            "collect_icheon_education_courses",
            municipal_icheon.ICHEON_LIBRARY_PROVIDER,
            municipal_icheon.ICHEON_LIBRARY_URL,
        ),
        (
            municipal_icheon,
            "collect_icheon_education_courses",
            municipal_icheon.ICHEON_ARTIC_PROVIDER,
            municipal_icheon.ICHEON_ARTIC_URL,
        ),
        (
            municipal_hongcheon,
            "collect_hongcheon_education_courses",
            municipal_hongcheon.HONGCHEON_LIBRARY_PROVIDER,
            municipal_hongcheon.HONGCHEON_LIBRARY_URL,
        ),
        (
            municipal_hongcheon,
            "collect_hongcheon_education_courses",
            municipal_hongcheon.HONGCHEON_EXISTING_COURSE_PROVIDER,
            municipal_hongcheon.HONGCHEON_EXISTING_COURSE_URL,
        ),
        (
            municipal_hongseong,
            "collect_hongseong_education",
            municipal_hongseong.HONGSEONG_PROVIDER,
            municipal_hongseong.HONGSEONG_CANONICAL_URL,
        ),
        (
            municipal_hapcheon,
            "collect_hapcheon_education",
            municipal_hapcheon.HAPCHEON_PROVIDER,
            municipal_hapcheon.HAPCHEON_URL,
        ),
        (
            municipal_buyeo,
            "collect_buyeo_education",
            municipal_buyeo.BUYEO_PROVIDER,
            municipal_buyeo.BUYEO_CANONICAL_URL,
        ),
        (
            municipal_buan,
            "collect_buan_education",
            municipal_buan.BUAN_PROVIDER,
            municipal_buan.BUAN_CANONICAL_URL,
        ),
        (
            municipal_bonghwa,
            "collect_bonghwa_education",
            municipal_bonghwa.BONGHWA_PROVIDER,
            municipal_bonghwa.BONGHWA_CANONICAL_URL,
        ),
        (
            municipal_gimcheon,
            "collect_gimcheon_education",
            municipal_gimcheon.GIMCHEON_PROVIDER,
            municipal_gimcheon.GIMCHEON_CANONICAL_URL,
        ),
        (
            municipal_cheongsong,
            "collect_cheongsong_education",
            municipal_cheongsong.CHEONGSONG_PROVIDER,
            municipal_cheongsong.CHEONGSONG_CANONICAL_URL,
        ),
        (
            municipal_chilgok,
            "collect_chilgok_education",
            municipal_chilgok.CHILGOK_PROVIDER,
            municipal_chilgok.CHILGOK_CANONICAL_URL,
        ),
        (
            municipal_cheongdo,
            "collect_cheongdo_education",
            municipal_cheongdo.CHEONGDO_LIFELONG_PROVIDER,
            municipal_cheongdo.CHEONGDO_LEDGER_BY_PROVIDER[
                municipal_cheongdo.CHEONGDO_LIFELONG_PROVIDER
            ].url,
        ),
        (
            municipal_cheongdo,
            "collect_cheongdo_education",
            municipal_cheongdo.CHEONGDO_YOUTH_PROVIDER,
            municipal_cheongdo.CHEONGDO_LEDGER_BY_PROVIDER[
                municipal_cheongdo.CHEONGDO_YOUTH_PROVIDER
            ].url,
        ),
        (
            municipal_cheongdo,
            "collect_cheongdo_education",
            municipal_cheongdo.CHEONGDO_WOMEN_PROVIDER,
            municipal_cheongdo.CHEONGDO_LEDGER_BY_PROVIDER[
                municipal_cheongdo.CHEONGDO_WOMEN_PROVIDER
            ].url,
        ),
        (
            municipal_gyeongsan,
            "collect_gyeongsan_education",
            municipal_gyeongsan.GYEONGSAN_TOWN_PROVIDER,
            municipal_gyeongsan.GYEONGSAN_TOWN_CANONICAL_URL,
        ),
        (
            municipal_gyeongsan,
            "collect_gyeongsan_education",
            municipal_gyeongsan.GYEONGSAN_PROGRAM_PROVIDER,
            municipal_gyeongsan.GYEONGSAN_PROGRAM_CANONICAL_URL,
        ),
        (
            municipal_gwacheon,
            "collect_gwacheon_education",
            municipal_gwacheon.GWACHEON_PROVIDER,
            municipal_gwacheon.GWACHEON_CANONICAL_URL,
        ),
        (
            municipal_gyeryong,
            "collect_gyeryong_education",
            municipal_gyeryong.GYERYONG_PROVIDER,
            municipal_gyeryong.GYERYONG_URL,
        ),
        (
            municipal_paju,
            "collect_paju_education",
            municipal_paju.PAJU_PROVIDER,
            municipal_paju.PAJU_URL,
        ),
        (
            municipal_namyangju,
            "collect_namyangju_education",
            municipal_namyangju.NAMYANGJU_PROVIDER,
            municipal_namyangju.NAMYANGJU_CANONICAL_URL,
        ),
        (
            municipal_gimhae,
            "collect_gimhae_education",
            municipal_gimhae.GIMHAE_PROVIDER,
            municipal_gimhae.GIMHAE_LEDGER_URL,
        ),
        (
            municipal_geochang,
            "collect_geochang_education",
            municipal_geochang.GEOCHANG_PROVIDER,
            municipal_geochang.GEOCHANG_CANONICAL_URL,
        ),
        (
            municipal_gyeongju,
            "collect_gyeongju_education",
            municipal_gyeongju.GYEONGJU_PROVIDER,
            municipal_gyeongju.GYEONGJU_INTEGRATED_URL,
        ),
        (
            municipal_goryeong,
            "collect_goryeong_education",
            municipal_goryeong.GORYEONG_PROVIDER,
            municipal_goryeong.GORYEONG_CANONICAL_URL,
        ),
        (
            municipal_haman,
            "collect_haman_education",
            municipal_haman.HAMAN_PROVIDER,
            municipal_haman.HAMAN_CANONICAL_URL,
        ),
        (
            municipal_sangju,
            "collect_sangju_education",
            municipal_sangju.SANGJU_PROVIDER,
            municipal_sangju.SANGJU_CANONICAL_URL,
        ),
        (
            municipal_seosan,
            "collect_seosan_education",
            municipal_seosan.SEOSAN_PROVIDER,
            municipal_seosan.SEOSAN_CANONICAL_URL,
        ),
        (
            municipal_seocheon,
            "collect_seocheon_education",
            municipal_seocheon.SEOCHEON_PROVIDER,
            municipal_seocheon.SEOCHEON_CANONICAL_URL,
        ),
        (
            municipal_wonju,
            "collect_wonju_education",
            municipal_wonju.WONJU_MUNICIPAL_PROVIDER,
            municipal_wonju.WONJU_MUNICIPAL_URL,
        ),
        (
            municipal_wonju,
            "collect_wonju_education",
            municipal_wonju.WONJU_GWE_PROVIDER,
            municipal_wonju.WONJU_GWE_URL,
        ),
        (
            municipal_yangsan,
            "collect_yangsan_education",
            municipal_yangsan.YANGSAN_LIFELONG_PROVIDER,
            municipal_yangsan.YANGSAN_LIFELONG_CANONICAL_URL,
        ),
        (
            municipal_yangsan,
            "collect_yangsan_education",
            municipal_yangsan.YANGSAN_BOOKING_PROVIDER,
            municipal_yangsan.YANGSAN_BOOKING_CANONICAL_URL,
        ),
        (
            municipal_seongju,
            "collect_seongju_education",
            municipal_seongju.SEONGJU_PROVIDER,
            municipal_seongju.SEONGJU_CANONICAL_URL,
        ),
        (
            municipal_uiseong,
            "collect_uiseong_education",
            municipal_uiseong.UISEONG_PROVIDER,
            municipal_uiseong.UISEONG_CANONICAL_URL,
        ),
        (
            municipal_yeongdeok,
            "collect_yeongdeok_education",
            municipal_yeongdeok.YEONGDEOK_PROVIDER,
            municipal_yeongdeok.YEONGDEOK_CANONICAL_URL,
        ),
        (
            municipal_yeongyang,
            "collect_yeongyang_education",
            municipal_yeongyang.YEONGYANG_PROVIDER,
            municipal_yeongyang.YEONGYANG_CANONICAL_URL,
        ),
        (
            municipal_jinan,
            "collect_jinan_education",
            municipal_jinan.JINAN_PROVIDER,
            municipal_jinan.JINAN_CANONICAL_URL,
        ),
        (
            municipal_jangsu,
            "collect_jangsu_education",
            municipal_jangsu.JANGSU_PROVIDER,
            municipal_jangsu.JANGSU_CANONICAL_URL,
        ),
        (
            municipal_jeongeup,
            "collect_jeongeup_education",
            municipal_jeongeup.JEONGEUP_PROVIDER,
            municipal_jeongeup.JEONGEUP_CANONICAL_URL,
        ),
        (
            municipal_sunchang,
            "collect_sunchang_education",
            municipal_sunchang.SUNCHANG_PROVIDER,
            municipal_sunchang.SUNCHANG_CANONICAL_URL,
        ),
        (
            municipal_imsil,
            "collect_imsil_education",
            municipal_imsil.IMSIL_PROVIDER,
            municipal_imsil.IMSIL_URL,
        ),
        (
            municipal_uljin,
            "collect_uljin_education",
            municipal_uljin.ULJIN_PROVIDER,
            municipal_uljin.ULJIN_CANONICAL_URL,
        ),
        (
            municipal_ulleung,
            "collect_ulleung_education",
            municipal_ulleung.ULLEUNG_FAMILY_PROVIDER,
            municipal_ulleung.ULLEUNG_FAMILY_URL,
        ),
        (
            municipal_ulleung,
            "collect_ulleung_education",
            municipal_ulleung.ULLEUNG_LIFELONG_PROVIDER,
            municipal_ulleung.ULLEUNG_LIFELONG_URL,
        ),
        (
            municipal_taebaek,
            "collect_taebaek_education",
            municipal_taebaek.TAEBAEK_PROVIDER,
            municipal_taebaek.TAEBAEK_CANONICAL_URL,
        ),
        (
            municipal_muju,
            "collect_muju_education",
            municipal_muju.MUJU_PROVIDER,
            municipal_muju.MUJU_URL,
        ),
        (
            municipal_osan,
            "collect_osan_education",
            municipal_osan.OSAN_PROVIDER,
            municipal_osan.OSAN_CANONICAL_URL,
        ),
        (
            municipal_wanju,
            "collect_wanju_education",
            municipal_wanju.WANJU_PROVIDER,
            municipal_wanju.WANJU_CANONICAL_URL,
        ),
        (
            municipal_nonsan,
            "collect_nonsan_education",
            municipal_nonsan.NONSAN_PROVIDER,
            municipal_nonsan.NONSAN_CANONICAL_URL,
        ),
        (
            municipal_namdong,
            "collect_namdong_education",
            municipal_namdong.NAMDONG_PROVIDER,
            municipal_namdong.NAMDONG_CANONICAL_URL,
        ),
        (
            municipal_incheon_ganghwa,
            "collect_incheon_ganghwa_education",
            municipal_incheon_ganghwa.GANGHWA_PROVIDER,
            municipal_incheon_ganghwa.GANGHWA_CANONICAL_URL,
        ),
        (
            municipal_incheon_ongjin,
            "collect_incheon_ongjin_education",
            municipal_incheon_ongjin.ONGJIN_PROVIDER,
            municipal_incheon_ongjin.ONGJIN_CANONICAL_URL,
        ),
        (
            municipal_incheon_yeonsu,
            "collect_incheon_yeonsu_education",
            municipal_incheon_yeonsu.YEONSU_PROVIDER,
            municipal_incheon_yeonsu.YEONSU_CANONICAL_URL,
        ),
        (
            municipal_incheon_gyeyang,
            "collect_incheon_gyeyang_education",
            municipal_incheon_gyeyang.GYEYANG_PROVIDER,
            municipal_incheon_gyeyang.GYEYANG_URL,
        ),
    ),
)
def test_shared_router_dispatches_exact_specialized_owner(
    monkeypatch: pytest.MonkeyPatch,
    module: object,
    collector_name: str,
    provider: str,
    url: str,
) -> None:
    from Crawler import Crawler_MunicipalYaml as municipal

    sentinel = ([{"title": provider}], "specialized-parser", {"snapshot_complete": True})
    monkeypatch.setattr(module, collector_name, lambda *_args, **_kwargs: sentinel)
    target = municipal.CrawlTarget(
        provider=provider,
        name="교육 전용 테스트",
        branch="교육 전용 테스트",
        url=url,
        source="test",
    )

    assert municipal.collect_from_url(
        target,
        timeout=1,
        max_depth=0,
        max_pages=40,
        detail_limit=120,
    ) == sentinel
