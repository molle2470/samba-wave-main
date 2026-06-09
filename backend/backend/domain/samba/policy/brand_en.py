"""브랜드 국문 → 영문 매핑.

상품명 조합의 {브랜드명_영문} 태그에서 사용. 국문 브랜드명을 공식 영문 표기로 변환해
"DYNAFIT 다이나핏" 처럼 영문+국문 병기를 가능하게 한다.

- 매핑에 없는 브랜드는 빈 문자열 반환 → 조합 시 태그가 자동 제거(국문만 노출)되어 오역 방지.
- 신규 브랜드는 여기에 추가하면 즉시 반영.
- 표기 불확실한 자체/편집샵 브랜드(와키윌리, 마스마룰즈, 르무통, 셀렙샵에디션 등)는
  의도적으로 비워둠 — 잘못된 영문 노출보다 국문 단독이 안전.
"""

# 키: samba_collected_product.brand 의 국문 원본값(공백/표기 그대로)
BRAND_EN_MAP: dict[str, str] = {
    "다이나핏": "DYNAFIT",
    "아이더": "EIDER",
    "내셔널지오그래픽": "NATIONAL GEOGRAPHIC",
    "내셔널지오그래픽키즈": "NATIONAL GEOGRAPHIC KIDS",
    "아디다스": "ADIDAS",
    "아디다스 오리지널스": "ADIDAS ORIGINALS",
    "나이키": "NIKE",
    "나이키 스윔": "NIKE SWIM",
    "나이키 키즈": "NIKE KIDS",
    "나이키 골프": "NIKE GOLF",
    "노스페이스": "THE NORTH FACE",
    "라코스테": "LACOSTE",
    "에잇세컨즈": "8SECONDS",
    "커버낫": "COVERNAT",
    "스파이더": "SPYDER",
    "푸마": "PUMA",
    "푸마 키즈": "PUMA KIDS",
    "지오다노": "GIORDANO",
    "엄브로": "UMBRO",
    "디스커버리": "DISCOVERY",
    "디스커버리 익스페디션": "DISCOVERY EXPEDITION",
    "스노우피크 어패럴": "SNOW PEAK APPAREL",
    "룰루레몬": "LULULEMON",
    "스케쳐스": "SKECHERS",
    "스케쳐스USA": "SKECHERS USA",
    "엠포리오 아르마니": "EMPORIO ARMANI",
    "엠포리오 아르마니 언더웨어": "EMPORIO ARMANI UNDERWEAR",
    "디스이즈네버댓": "THISISNEVERTHAT",
    "닥스 ACC": "DAKS ACC",
    "뉴발란스": "NEW BALANCE",
    "아레나": "ARENA",
    "아레나 키즈": "ARENA KIDS",
    "데상트": "DESCENTE",
    "엠엘비": "MLB",
    "케이투": "K2",
    "반스": "VANS",
    "예일": "YALE",
    "노르디스크": "NORDISK",
    "리": "LEE",
    "네파": "NEPA",
    "휠라": "FILA",
    "휠라키즈": "FILA KIDS",
    "휠라언더웨어(백화점)": "FILA UNDERWEAR",
    "휠라 선글라스(백화점)": "FILA",
    "크록스": "CROCS",
    "킨": "KEEN",
    "탑텐": "TOPTEN",
    "밸롭": "BALLOP",
    "블랙야크": "BLACKYAK",
    "캘빈클라인 진": "CALVIN KLEIN JEANS",
    "캘빈클라인 언더웨어": "CALVIN KLEIN UNDERWEAR",
    "게스": "GUESS",
    "게스언더웨어": "GUESS UNDERWEAR",
    "르꼬끄": "LE COQ SPORTIF",
    "리바이스": "LEVI'S",
    "코오롱스포츠": "KOLON SPORT",
    "미즈노": "MIZUNO",
    "지프": "JEEP",
    "밀레": "MILLET",
    "오니츠카타이거": "ONITSUKA TIGER",
    "살로몬": "SALOMON",
    "코데즈컴바인 이너웨어": "CODES COMBINE INNERWEAR",
    "파타고니아": "PATAGONIA",
    "조던": "JORDAN",
    "닥터마틴": "DR. MARTENS",
    "잔스포츠": "JANSPORT",
}


def brand_en(brand_kr: str | None) -> str:
    """국문 브랜드명 → 영문 표기. 매핑 없으면 빈 문자열."""
    if not brand_kr:
        return ""
    return BRAND_EN_MAP.get(brand_kr.strip(), "")
