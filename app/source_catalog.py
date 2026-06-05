from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SourceCatalogEntry:
    priority: int
    group_key: str
    group_label: str
    institution_name: str
    institution_code: str
    source_key: str
    source_type: str
    adapter_name: str
    base_url: str
    crawl_frequency: str
    status: str
    expected_formats: tuple[str, ...] = field(default_factory=tuple)
    notes: str = ""


BUSAN_EXPENSE_URL = "https://www.busan.go.kr/ghopen12"


def _source(
    priority: int,
    group_key: str,
    group_label: str,
    name: str,
    code: str,
    base_url: str,
    adapter: str,
    status: str,
    source_type: str = "public_board",
    frequency: str = "daily",
    expected_formats: tuple[str, ...] = ("xlsx", "xls", "hwp", "pdf"),
    notes: str = "",
    source_key: str | None = None,
) -> SourceCatalogEntry:
    return SourceCatalogEntry(
        priority=priority,
        group_key=group_key,
        group_label=group_label,
        institution_name=name,
        institution_code=code,
        source_key=source_key or f"{code}_expense",
        source_type=source_type,
        adapter_name=adapter,
        base_url=base_url,
        crawl_frequency=frequency,
        status=status,
        expected_formats=expected_formats,
        notes=notes,
    )


def _busan_city_source(name: str, code: str, notes: str = "") -> SourceCatalogEntry:
    return _source(
        1,
        "busan_city_core",
        "부산광역시 본청/의회/직속기관/사업소",
        name,
        code,
        BUSAN_EXPENSE_URL,
        "busan_go_kr_expense_board",
        "crawl_target_ready",
        expected_formats=("xlsx", "xls", "hwp", "pdf"),
        notes=notes or "부산시 업무추진비 통합 게시판에서 수집",
    )


def _planned(
    priority: int,
    group_key: str,
    group_label: str,
    name: str,
    code: str,
    base_url: str,
    notes: str = "",
    frequency: str = "weekly",
) -> SourceCatalogEntry:
    return _source(
        priority,
        group_key,
        group_label,
        name,
        code,
        base_url,
        "adapter_pending",
        "adapter_pending",
        frequency=frequency,
        notes=notes or "공개 경로와 첨부 포맷 확인 후 adapter 추가",
    )


SOURCE_CATALOG: tuple[SourceCatalogEntry, ...] = (
    _source(
        1,
        "busan_city_core",
        "부산광역시 본청/의회/직속기관/사업소",
        "부산광역시청",
        "busan_city",
        BUSAN_EXPENSE_URL,
        "busan_go_kr_expense_board",
        "crawl_target_ready",
        expected_formats=("xlsx", "xls", "hwp", "pdf"),
        notes="현재 fixture 파이프라인의 기준 source이며, 다음 단계에서 live parser로 교체",
        source_key="busan_city_expense_v1",
    ),
    _busan_city_source("부산광역시의회", "busan_council", "부산시 관련기관 링크 및 의회 정보공개 게시판 확인 대상"),
    _busan_city_source("부산소방재난본부", "busan_fire_headquarters"),
    _busan_city_source("부산광역시 자치경찰위원회", "busan_autonomous_police_committee"),
    _busan_city_source("부산광역시 인재개발원", "busan_hrd_institute"),
    _busan_city_source("부산광역시 보건환경연구원", "busan_health_environment_institute"),
    _busan_city_source("부산광역시 농업기술센터", "busan_agricultural_technology_center"),
    _busan_city_source("부산소방학교", "busan_fire_academy"),
    _busan_city_source("상수도사업본부", "busan_waterworks"),
    _busan_city_source("건설본부", "busan_construction_headquarters"),
    _busan_city_source("낙동강관리본부", "busan_nakdong_river_management"),
    _busan_city_source("서울본부", "busan_seoul_office"),
    _busan_city_source("여성회관", "busan_womens_center"),
    _busan_city_source("여성문화회관", "busan_womens_culture_center"),
    _busan_city_source("아동보호종합센터", "busan_child_protection_center"),
    _busan_city_source("차량등록사업소", "busan_vehicle_registration"),
    _busan_city_source("클래식부산", "busan_classic_busan"),
    _busan_city_source("시립박물관", "busan_museum"),
    _busan_city_source("시립미술관", "busan_art_museum"),
    _busan_city_source("현대미술관", "busan_moca"),
    _busan_city_source("부산근현대역사관", "busan_modern_history_museum"),
    _busan_city_source("부산도서관", "busan_library"),
    _busan_city_source("충렬사관리사무소", "busan_chungnyeolsa_office"),
    _busan_city_source("체육시설관리사업소", "busan_sports_facility_management"),
    _busan_city_source("금련산청소년수련원", "busan_geumnyeonsan_youth_center"),
    _busan_city_source("푸른도시가꾸기사업소", "busan_green_city_office"),
    _busan_city_source("건설안전시험사업소", "busan_construction_safety_test_office"),
    _busan_city_source("남항관리사업소", "busan_south_port_management"),
    _busan_city_source("엄궁농산물도매시장관리사업소", "busan_eomgung_market_office"),
    _busan_city_source("반여농산물도매시장관리사업소", "busan_banyeo_market_office"),
    _busan_city_source("국제수산물유통시설관리사업소", "busan_international_fish_market_office"),
    _busan_city_source("수산자원연구소", "busan_fisheries_resources_institute"),
    _busan_city_source("해양자연사박물관", "busan_marine_natural_history_museum"),
    _busan_city_source("교통정보서비스센터", "busan_transport_information_center"),
    _planned(2, "busan_local_public_enterprises", "부산시 산하 지방공기업", "부산교통공사", "busan_transportation_corporation", "https://www.humetro.busan.kr/"),
    _planned(2, "busan_local_public_enterprises", "부산시 산하 지방공기업", "부산도시공사", "busan_urban_corporation", "https://www.bmc.busan.kr/"),
    _planned(2, "busan_local_public_enterprises", "부산시 산하 지방공기업", "부산시설공단", "busan_facilities_corporation", "https://www.bisco.or.kr/"),
    _planned(2, "busan_local_public_enterprises", "부산시 산하 지방공기업", "부산환경공단", "busan_environment_corporation", "https://www.beco.or.kr/"),
    _planned(2, "busan_local_public_enterprises", "부산시 산하 지방공기업", "부산관광공사", "busan_tourism_organization", "https://www.bto.or.kr/"),
    _planned(3, "busan_invested_institutions", "부산시 출자/출연기관", "벡스코", "bexco", "https://www.bexco.co.kr/"),
    _planned(3, "busan_invested_institutions", "부산시 출자/출연기관", "아시아드CC", "busan_asiad_cc", "https://www.asiadcc.co.kr/"),
    _planned(3, "busan_invested_institutions", "부산시 출자/출연기관", "부산의료원", "busan_medical_center", "https://www.busanmc.or.kr/"),
    _planned(3, "busan_invested_institutions", "부산시 출자/출연기관", "부산여성가족과 평생교육진흥원", "busan_women_family_lifelong_education_institute", "https://www.bgli.re.kr/"),
    _planned(3, "busan_invested_institutions", "부산시 출자/출연기관", "부산연구원", "busan_development_institute", "https://www.bdi.re.kr/"),
    _planned(3, "busan_invested_institutions", "부산시 출자/출연기관", "부산경제진흥원", "busan_economic_promotion_agency", "https://www.bepa.kr/"),
    _planned(3, "busan_invested_institutions", "부산시 출자/출연기관", "부산신용보증재단", "busan_credit_guarantee_foundation", "https://www.busansinbo.or.kr/"),
    _planned(3, "busan_invested_institutions", "부산시 출자/출연기관", "부산디자인진흥원", "busan_design_promotion_institute", "https://www.dcb.or.kr/"),
    _planned(3, "busan_invested_institutions", "부산시 출자/출연기관", "부산테크노파크", "busan_technopark", "https://www.btp.or.kr/"),
    _planned(3, "busan_invested_institutions", "부산시 출자/출연기관", "부산정보산업진흥원", "busan_it_industry_promotion_agency", "https://www.busanit.or.kr/"),
    _planned(3, "busan_invested_institutions", "부산시 출자/출연기관", "부산광역시사회서비스원", "busan_social_service_agency", "https://www.busan.pass.or.kr/"),
    _planned(3, "busan_invested_institutions", "부산시 출자/출연기관", "부산문화재단", "busan_cultural_foundation", "https://www.bscf.or.kr/"),
    _planned(3, "busan_invested_institutions", "부산시 출자/출연기관", "영화의전당", "busan_cinema_center", "https://www.dureraum.org/"),
    _planned(3, "busan_invested_institutions", "부산시 출자/출연기관", "부산글로벌도시재단", "busan_global_city_foundation", "https://www.bfic.kr/"),
    _planned(3, "busan_invested_institutions", "부산시 출자/출연기관", "부산과학기술고등교육진흥원", "busan_institute_of_science_and_higher_education", "https://www.bistep.re.kr/"),
    _planned(3, "busan_invested_institutions", "부산시 출자/출연기관", "부산문화회관", "busan_cultural_center", "https://www.bscc.or.kr/"),
    _planned(4, "busan_education", "부산광역시교육청", "부산광역시교육청", "busan_office_of_education", "https://www.pen.go.kr/", "본청/교육지원청/직속기관 우선, 학교는 후속 확장"),
    _planned(5, "busan_police_prosecution", "부산 경찰/검찰", "부산경찰청", "busan_metropolitan_police", "https://www.bspolice.go.kr/", "경찰청 본청과 경찰서별 게시판을 단계적으로 수집"),
    _planned(5, "busan_police_prosecution", "부산 경찰/검찰", "부산고등검찰청", "busan_high_prosecutors_office", "https://www.spo.go.kr/site/highbusan/main.do", "검찰청 사전정보공표 업무추진비 집행내역"),
    _planned(5, "busan_police_prosecution", "부산 경찰/검찰", "부산지방검찰청", "busan_district_prosecutors_office", "https://www.spo.go.kr/site/busan/main.do"),
    _planned(5, "busan_police_prosecution", "부산 경찰/검찰", "부산지방검찰청 동부지청", "busan_east_branch_prosecutors_office", "https://www.spo.go.kr/site/eastbusan/main.do"),
    _planned(5, "busan_police_prosecution", "부산 경찰/검찰", "부산지방검찰청 서부지청", "busan_west_branch_prosecutors_office", "https://www.spo.go.kr/site/westbusan/main.do"),
    _planned(6, "busan_court_election", "부산 법원/선관위", "부산 법원 계열", "busan_courts", "https://busan.scourt.go.kr/", "업무추진비 공개 경로 확인 선행"),
    _planned(6, "busan_court_election", "부산 법원/선관위", "부산광역시선거관리위원회", "busan_election_commission", "https://bs.nec.go.kr/", "16개 구/군 선관위 포함 여부와 공개 경로 확인 선행"),
    _planned(6, "busan_court_election", "부산 법원/선관위", "부산광역시 중구선거관리위원회", "busan_junggu_election_commission", "https://bs.nec.go.kr/", "구/군 선관위 업무추진비 공개 경로 확인 선행"),
    _planned(6, "busan_court_election", "부산 법원/선관위", "부산광역시 서구선거관리위원회", "busan_seogu_election_commission", "https://bs.nec.go.kr/", "구/군 선관위 업무추진비 공개 경로 확인 선행"),
    _planned(6, "busan_court_election", "부산 법원/선관위", "부산광역시 동구선거관리위원회", "busan_donggu_election_commission", "https://bs.nec.go.kr/", "구/군 선관위 업무추진비 공개 경로 확인 선행"),
    _planned(6, "busan_court_election", "부산 법원/선관위", "부산광역시 영도구선거관리위원회", "busan_yeongdogu_election_commission", "https://bs.nec.go.kr/", "구/군 선관위 업무추진비 공개 경로 확인 선행"),
    _planned(6, "busan_court_election", "부산 법원/선관위", "부산광역시 부산진구선거관리위원회", "busan_busanjingu_election_commission", "https://bs.nec.go.kr/", "구/군 선관위 업무추진비 공개 경로 확인 선행"),
    _planned(6, "busan_court_election", "부산 법원/선관위", "부산광역시 동래구선거관리위원회", "busan_dongnaegu_election_commission", "https://bs.nec.go.kr/", "구/군 선관위 업무추진비 공개 경로 확인 선행"),
    _planned(6, "busan_court_election", "부산 법원/선관위", "부산광역시 남구선거관리위원회", "busan_namgu_election_commission", "https://bs.nec.go.kr/", "구/군 선관위 업무추진비 공개 경로 확인 선행"),
    _planned(6, "busan_court_election", "부산 법원/선관위", "부산광역시 북구선거관리위원회", "busan_bukgu_election_commission", "https://bs.nec.go.kr/", "구/군 선관위 업무추진비 공개 경로 확인 선행"),
    _planned(6, "busan_court_election", "부산 법원/선관위", "부산광역시 해운대구선거관리위원회", "busan_haeundaegu_election_commission", "https://bs.nec.go.kr/", "구/군 선관위 업무추진비 공개 경로 확인 선행"),
    _planned(6, "busan_court_election", "부산 법원/선관위", "부산광역시 사하구선거관리위원회", "busan_sahagu_election_commission", "https://bs.nec.go.kr/", "구/군 선관위 업무추진비 공개 경로 확인 선행"),
    _planned(6, "busan_court_election", "부산 법원/선관위", "부산광역시 금정구선거관리위원회", "busan_geumjeonggu_election_commission", "https://bs.nec.go.kr/", "구/군 선관위 업무추진비 공개 경로 확인 선행"),
    _planned(6, "busan_court_election", "부산 법원/선관위", "부산광역시 강서구선거관리위원회", "busan_gangseogu_election_commission", "https://bs.nec.go.kr/", "구/군 선관위 업무추진비 공개 경로 확인 선행"),
    _planned(6, "busan_court_election", "부산 법원/선관위", "부산광역시 연제구선거관리위원회", "busan_yeonjegu_election_commission", "https://bs.nec.go.kr/", "구/군 선관위 업무추진비 공개 경로 확인 선행"),
    _planned(6, "busan_court_election", "부산 법원/선관위", "부산광역시 수영구선거관리위원회", "busan_suyeonggu_election_commission", "https://bs.nec.go.kr/", "구/군 선관위 업무추진비 공개 경로 확인 선행"),
    _planned(6, "busan_court_election", "부산 법원/선관위", "부산광역시 사상구선거관리위원회", "busan_sasanggu_election_commission", "https://bs.nec.go.kr/", "구/군 선관위 업무추진비 공개 경로 확인 선행"),
    _planned(6, "busan_court_election", "부산 법원/선관위", "부산광역시 기장군선거관리위원회", "busan_gijanggun_election_commission", "https://bs.nec.go.kr/", "구/군 선관위 업무추진비 공개 경로 확인 선행"),
    _planned(7, "central_busan_offices", "중앙부처 부산 지방청", "부산지방국세청", "busan_regional_tax_office", "https://www.nts.go.kr/busan/"),
    _planned(7, "central_busan_offices", "중앙부처 부산 지방청", "부산본부세관", "busan_main_customs", "https://www.customs.go.kr/busan/"),
    _planned(7, "central_busan_offices", "중앙부처 부산 지방청", "부산지방고용노동청", "busan_employment_labor_office", "https://www.moel.go.kr/local/busan/"),
    _planned(7, "central_busan_offices", "중앙부처 부산 지방청", "부산지방해양수산청", "busan_regional_oceans_fisheries_office", "https://busan.mof.go.kr/"),
    _planned(7, "central_busan_offices", "중앙부처 부산 지방청", "부산지방기상청", "busan_regional_meteorological_office", "https://www.kma.go.kr/busan/"),
    _planned(7, "central_busan_offices", "중앙부처 부산 지방청", "부산지방보훈청", "busan_veterans_office", "https://www.mpva.go.kr/"),
    _planned(7, "central_busan_offices", "중앙부처 부산 지방청", "부산울산지방병무청", "busan_ulsan_military_manpower_office", "https://www.mma.go.kr/contents.do?mc=mma0002062"),
    _planned(7, "central_busan_offices", "중앙부처 부산 지방청", "부산지방조달청", "busan_public_procurement_service", "https://www.pps.go.kr/busan/"),
    _planned(7, "central_busan_offices", "중앙부처 부산 지방청", "동남지방통계청", "southeast_regional_statistics_office", "https://kostat.go.kr/"),
    _planned(7, "central_busan_offices", "중앙부처 부산 지방청", "부산지방우정청", "busan_regional_post_office", "https://www.koreapost.go.kr/bs/"),
    _planned(7, "central_busan_offices", "중앙부처 부산 지방청", "부산출입국·외국인청", "busan_immigration_office", "https://www.immigration.go.kr/"),
    _planned(7, "central_busan_offices", "중앙부처 부산 지방청", "부산교도소/부산구치소", "busan_correctional_institutions", "https://www.corrections.go.kr/"),
    _planned(8, "national_public_institutions_busan", "부산권 국가공기업/공공기관", "부산항만공사", "busan_port_authority", "https://www.busanpa.com/"),
    _planned(8, "national_public_institutions_busan", "부산권 국가공기업/공공기관", "부산항보안공사", "busan_port_security_corporation", "https://www.bpsc.co.kr/"),
    _planned(8, "national_public_institutions_busan", "부산권 국가공기업/공공기관", "한국자산관리공사", "kamco", "https://www.kamco.or.kr/"),
    _planned(8, "national_public_institutions_busan", "부산권 국가공기업/공공기관", "한국주택금융공사", "hf", "https://www.hf.go.kr/"),
    _planned(8, "national_public_institutions_busan", "부산권 국가공기업/공공기관", "기술보증기금", "kibo", "https://www.kibo.or.kr/"),
    _planned(8, "national_public_institutions_busan", "부산권 국가공기업/공공기관", "한국해양진흥공사", "korea_ocean_business_corporation", "https://www.kobc.or.kr/"),
)


def iter_source_catalog() -> tuple[SourceCatalogEntry, ...]:
    return SOURCE_CATALOG
