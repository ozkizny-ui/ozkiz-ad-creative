import streamlit as st
import requests
import pandas as pd
import json
from datetime import datetime
import anthropic
import time

st.set_page_config(page_title="오즈키즈 광고 소재 기획", page_icon="🎯", layout="wide")

# ── CSS ──────────────────────────────────────────────────────────────
st.markdown("""
<style>
    .main-header {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        padding: 2rem; border-radius: 12px; color: white;
        text-align: center; margin-bottom: 2rem;
    }
    .step-box {
        background: #f8f9fa; border-left: 4px solid #667eea;
        padding: 1rem 1.5rem; border-radius: 8px; margin: 1rem 0;
    }
    .usp-card {
        background: white; border: 1px solid #e0e0e0;
        border-radius: 10px; padding: 1.2rem; margin: 0.5rem 0;
        box-shadow: 0 2px 8px rgba(0,0,0,0.06);
    }
    .ad-card {
        background: linear-gradient(135deg, #f5f7fa, #c3cfe2);
        border-radius: 10px; padding: 1.2rem; margin: 0.5rem 0;
    }
    .copy-text {
        background: #fff3cd; border-radius: 8px;
        padding: 1rem; font-size: 1.1rem; font-weight: 600;
        border-left: 4px solid #ffc107;
    }
    .review-box {
        background: #f0f4ff; border-radius: 8px;
        padding: 0.8rem; margin: 0.3rem 0; font-size: 0.9rem;
    }
    .inventory-good { color: #28a745; font-weight: 600; }
    .inventory-low  { color: #dc3545; font-weight: 600; }
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="main-header">
    <h1>🎯 오즈키즈 광고 소재 기획 도구</h1>
    <p>제품명 입력 → 상품 분석 → USP 도출 → 광고 문구 & 소재 자동 기획</p>
</div>
""", unsafe_allow_html=True)

# ── Secrets & 상수 ───────────────────────────────────────────────────
CLIENT_ID     = st.secrets.get("CAFE24_CLIENT_ID", "")
CLIENT_SECRET = st.secrets.get("CAFE24_CLIENT_SECRET", "")
MALL_ID       = st.secrets.get("CAFE24_MALL_ID", "ozkiz")
ANTHROPIC_KEY = st.secrets.get("ANTHROPIC_API_KEY", "")

# ── Session State 초기화 ──────────────────────────────────────────────
for key in ["access_token", "product_data", "reviews", "inventory_df",
            "usp_result", "ad_copies", "ad_concepts"]:
    if key not in st.session_state:
        st.session_state[key] = None

# ════════════════════════════════════════════════════════════════════
# 카페24 API 함수
# ════════════════════════════════════════════════════════════════════

def get_access_token(auth_code: str) -> dict:
    """인증 코드로 Access Token 발급"""
    import base64
    credentials = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    r = requests.post(
        f"https://{MALL_ID}.cafe24api.com/api/v2/oauth/token",
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "authorization_code",
            "code": auth_code,
            "redirect_uri": "https://ozkiz.streamlit.app/callback",
        },
    )
    return r.json()

def refresh_access_token(refresh_token: str) -> dict:
    """Refresh Token으로 Access Token 갱신"""
    import base64
    credentials = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    r = requests.post(
        f"https://{MALL_ID}.cafe24api.com/api/v2/oauth/token",
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
    )
    return r.json()

def search_products(keyword: str, token: str) -> list:
    """상품명으로 상품 검색"""
    r = requests.get(
        f"https://{MALL_ID}.cafe24api.com/api/v2/admin/products",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        params={"product_name": keyword, "limit": 10, "embed": "options,variants"},
    )
    data = r.json()
    return data.get("products", [])

def get_product_reviews(product_no: int, token: str) -> list:
    """상품 리뷰 조회"""
    r = requests.get(
        f"https://{MALL_ID}.cafe24api.com/api/v2/admin/products/{product_no}/reviews",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        params={"limit": 50},
    )
    data = r.json()
    return data.get("reviews", [])

def get_product_detail(product_no: int, token: str) -> dict:
    """상품 상세 정보 조회"""
    r = requests.get(
        f"https://{MALL_ID}.cafe24api.com/api/v2/admin/products/{product_no}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        params={"embed": "options,variants,inventories"},
    )
    return r.json().get("product", {})

# ════════════════════════════════════════════════════════════════════
# Claude AI 함수
# ════════════════════════════════════════════════════════════════════

def analyze_usp_and_ads(product: dict, reviews: list, inventory_df=None) -> dict:
    """Claude로 USP 분석 + 광고 문구 + 소재 기획"""
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

    # 리뷰 텍스트 추출
    review_texts = []
    for r in reviews[:20]:
        content = r.get("content", "") or r.get("review_content", "")
        if content:
            review_texts.append(content)

    # 재고 정보
    inventory_info = ""
    if inventory_df is not None and not inventory_df.empty:
        inventory_info = f"\n\n[재고 현황]\n{inventory_df.to_string(index=False)}"

    prompt = f"""당신은 오즈키즈(영유아 제품 브랜드) 전문 마케터입니다.
아래 상품 정보와 고객 리뷰를 분석해서 광고 소재를 기획해주세요.

[상품 정보]
- 상품명: {product.get('product_name', '')}
- 판매가: {product.get('price', '')}원
- 상품 설명: {str(product.get('description', ''))[:500]}
- 상세 설명: {str(product.get('detail_image', ''))[:200]}{inventory_info}

[실제 고객 리뷰 {len(review_texts)}개]
{chr(10).join([f"- {r}" for r in review_texts[:15]])}

다음 JSON 형식으로 정확히 응답해주세요 (마크다운 없이 순수 JSON만):
{{
  "usp_list": [
    {{"title": "USP 제목", "desc": "한 줄 설명", "evidence": "리뷰나 상품 정보 근거"}},
    {{"title": "USP 제목", "desc": "한 줄 설명", "evidence": "리뷰나 상품 정보 근거"}},
    {{"title": "USP 제목", "desc": "한 줄 설명", "evidence": "리뷰나 상품 정보 근거"}},
    {{"title": "USP 제목", "desc": "한 줄 설명", "evidence": "리뷰나 상품 정보 근거"}},
    {{"title": "USP 제목", "desc": "한 줄 설명", "evidence": "리뷰나 상품 정보 근거"}}
  ],
  "ad_copies": [
    {{"headline": "헤드라인 (15자 이내)", "body": "본문 (40자 이내)", "cta": "CTA 문구", "tone": "말투 설명"}},
    {{"headline": "헤드라인 (15자 이내)", "body": "본문 (40자 이내)", "cta": "CTA 문구", "tone": "말투 설명"}},
    {{"headline": "헤드라인 (15자 이내)", "body": "본문 (40자 이내)", "cta": "CTA 문구", "tone": "말투 설명"}},
    {{"headline": "헤드라인 (15자 이내)", "body": "본문 (40자 이내)", "cta": "CTA 문구", "tone": "말투 설명"}},
    {{"headline": "헤드라인 (15자 이내)", "body": "본문 (40자 이내)", "cta": "CTA 문구", "tone": "말투 설명"}}
  ],
  "ad_concepts": [
    {{"concept": "소재 컨셉명", "format": "광고 형식 (예: 카드뉴스/릴스/배너)", "scenario": "구체적 연출 시나리오", "key_visual": "핵심 비주얼 설명", "target_moment": "타겟 상황/모먼트"}},
    {{"concept": "소재 컨셉명", "format": "광고 형식", "scenario": "구체적 연출 시나리오", "key_visual": "핵심 비주얼 설명", "target_moment": "타겟 상황/모먼트"}},
    {{"concept": "소재 컨셉명", "format": "광고 형식", "scenario": "구체적 연출 시나리오", "key_visual": "핵심 비주얼 설명", "target_moment": "타겟 상황/모먼트"}},
    {{"concept": "소재 컨셉명", "format": "광고 형식", "scenario": "구체적 연출 시나리오", "key_visual": "핵심 비주얼 설명", "target_moment": "타겟 상황/모먼트"}},
    {{"concept": "소재 컨셉명", "format": "광고 형식", "scenario": "구체적 연출 시나리오", "key_visual": "핵심 비주얼 설명", "target_moment": "타겟 상황/모먼트"}}
  ]
}}"""

    message = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=3000,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = message.content[0].text.strip()
    # JSON 파싱
    try:
        return json.loads(raw)
    except:
        start = raw.find("{")
        end   = raw.rfind("}") + 1
        return json.loads(raw[start:end])

# ════════════════════════════════════════════════════════════════════
# STEP 1: 카페24 인증
# ════════════════════════════════════════════════════════════════════

st.markdown("## STEP 1. 카페24 인증")

# Refresh Token이 secrets에 있으면 자동 갱신 시도
if not st.session_state.access_token:
    saved_refresh = st.secrets.get("CAFE24_REFRESH_TOKEN", "")
    if saved_refresh:
        with st.spinner("저장된 토큰으로 자동 인증 중..."):
            result = refresh_access_token(saved_refresh)
            if "access_token" in result:
                st.session_state.access_token = result["access_token"]
                st.success("✅ 자동 인증 성공!")

if st.session_state.access_token:
    st.success("✅ 카페24 인증 완료")
else:
    auth_url = (
        f"https://{MALL_ID}.cafe24api.com/api/v2/oauth/authorize"
        f"?response_type=code"
        f"&client_id={CLIENT_ID}"
        f"&state=ozkiz_ad"
        f"&redirect_uri=https://ozkiz.streamlit.app/callback"
        f"&scope=mall.read_product,mall.read_community,mall.read_store"
    )
    st.markdown(f"""
    <div class="step-box">
    <b>카페24 로그인이 필요합니다.</b><br><br>
    아래 버튼을 클릭하면 카페24 인증 페이지로 이동합니다.<br>
    인증 완료 후 URL에서 <code>code=</code> 뒤의 값을 복사해서 아래에 붙여넣으세요.
    </div>
    """, unsafe_allow_html=True)
    st.link_button("🔐 카페24 인증하기", auth_url)

    # URL 파라미터에서 code 자동 감지
    params = st.query_params
    if "code" in params:
        auto_code = params["code"]
        st.info(f"인증 코드 감지됨: `{auto_code[:10]}...` — 아래 버튼으로 토큰 발급")
        if st.button("토큰 발급"):
            with st.spinner("토큰 발급 중..."):
                result = get_access_token(auto_code)
                if "access_token" in result:
                    st.session_state.access_token = result["access_token"]
                    st.success("✅ 인증 성공! Refresh Token을 Secrets에 저장하세요:")
                    st.code(f'CAFE24_REFRESH_TOKEN = "{result.get("refresh_token", "")}"')
                    st.rerun()
                else:
                    st.error(f"인증 실패: {result}")
    else:
        code_input = st.text_input("또는 인증 코드 직접 입력", placeholder="code 값 붙여넣기")
        if st.button("토큰 발급") and code_input:
            with st.spinner("토큰 발급 중..."):
                result = get_access_token(code_input.strip())
                if "access_token" in result:
                    st.session_state.access_token = result["access_token"]
                    st.success("✅ 인증 성공! Refresh Token을 Secrets에 저장하세요:")
                    st.code(f'CAFE24_REFRESH_TOKEN = "{result.get("refresh_token", "")}"')
                    st.rerun()
                else:
                    st.error(f"인증 실패: {result}")

st.divider()

# ════════════════════════════════════════════════════════════════════
# STEP 2: 제품명 입력 & 상품 검색
# ════════════════════════════════════════════════════════════════════

st.markdown("## STEP 2. 제품명 입력")

col1, col2 = st.columns([3, 1])
with col1:
    product_keyword = st.text_input("제품명 입력", placeholder="예: 영아 장갑, 아기 벙어리장갑")
with col2:
    search_btn = st.button("🔍 상품 검색", use_container_width=True,
                           disabled=not st.session_state.access_token)

if search_btn and product_keyword:
    with st.spinner("상품 검색 중..."):
        products = search_products(product_keyword, st.session_state.access_token)
    if products:
        st.success(f"상품 {len(products)}개 발견!")
        options = {f"{p['product_name']} (No.{p['product_no']})": p for p in products}
        selected = st.selectbox("분석할 상품 선택", list(options.keys()))
        if st.button("이 상품으로 분석"):
            chosen = options[selected]
            with st.spinner("상품 상세 & 리뷰 불러오는 중..."):
                detail  = get_product_detail(chosen["product_no"], st.session_state.access_token)
                reviews = get_product_reviews(chosen["product_no"], st.session_state.access_token)
            st.session_state.product_data = detail if detail else chosen
            st.session_state.reviews = reviews
            st.success(f"✅ 리뷰 {len(reviews)}개 수집 완료!")
    else:
        st.warning("검색 결과가 없습니다. 다른 키워드로 시도해보세요.")

# 상품 정보 표시
if st.session_state.product_data:
    p = st.session_state.product_data
    with st.expander("📦 상품 정보 확인", expanded=False):
        c1, c2 = st.columns(2)
        with c1:
            st.markdown(f"**상품명:** {p.get('product_name', '-')}")
            st.markdown(f"**판매가:** {p.get('price', '-')}원")
            st.markdown(f"**상품번호:** {p.get('product_no', '-')}")
        with c2:
            url = f"https://{MALL_ID}.cafe24.com/product/detail.html?product_no={p.get('product_no', '')}"
            st.markdown(f"**상품 URL:** [바로가기]({url})")

    if st.session_state.reviews:
        with st.expander(f"💬 수집된 리뷰 ({len(st.session_state.reviews)}개)", expanded=False):
            for rv in st.session_state.reviews[:10]:
                content = rv.get("content") or rv.get("review_content", "")
                rating  = rv.get("rating", "")
                if content:
                    st.markdown(f"""<div class="review-box">⭐ {rating} | {content}</div>""",
                                unsafe_allow_html=True)

st.divider()

# ════════════════════════════════════════════════════════════════════
# STEP 3: 이지어드민 재고 업로드
# ════════════════════════════════════════════════════════════════════

st.markdown("## STEP 3. 재고 현황 업로드 (이지어드민 엑셀)")

uploaded = st.file_uploader(
    "이지어드민에서 다운받은 엑셀 파일을 업로드하세요",
    type=["xlsx", "xls", "csv"],
    help="이지어드민 → 재고현황 → 엑셀 다운로드"
)

if uploaded:
    try:
        if uploaded.name.endswith(".csv"):
            df = pd.read_csv(uploaded, encoding="utf-8-sig")
        else:
            df = pd.read_excel(uploaded)
        st.session_state.inventory_df = df
        st.success(f"✅ 재고 파일 업로드 완료 ({len(df)}행)")
        st.dataframe(df.head(10), use_container_width=True)
    except Exception as e:
        st.error(f"파일 읽기 오류: {e}")
else:
    st.info("재고 파일 없이도 분석 가능합니다. (선택 사항)")

st.divider()

# ════════════════════════════════════════════════════════════════════
# STEP 4: AI 분석 실행
# ════════════════════════════════════════════════════════════════════

st.markdown("## STEP 4. USP & 광고 소재 AI 분석")

analyze_btn = st.button(
    "🚀 AI 분석 시작 (USP + 광고 문구 + 소재 기획)",
    use_container_width=True,
    disabled=not st.session_state.product_data,
    type="primary"
)

if analyze_btn:
    with st.spinner("Claude AI가 분석 중입니다... (30초~1분 소요)"):
        try:
            result = analyze_usp_and_ads(
                st.session_state.product_data,
                st.session_state.reviews or [],
                st.session_state.inventory_df,
            )
            st.session_state.usp_result    = result.get("usp_list", [])
            st.session_state.ad_copies     = result.get("ad_copies", [])
            st.session_state.ad_concepts   = result.get("ad_concepts", [])
            st.success("✅ 분석 완료!")
        except Exception as e:
            st.error(f"분석 오류: {e}")

# ── USP 출력 ──────────────────────────────────────────────────────
if st.session_state.usp_result:
    st.markdown("### 🏆 제품 USP 5가지")
    for i, usp in enumerate(st.session_state.usp_result, 1):
        st.markdown(f"""
        <div class="usp-card">
            <b>USP {i}. {usp.get('title', '')}</b><br>
            {usp.get('desc', '')}<br>
            <small style="color:#888">📌 근거: {usp.get('evidence', '')}</small>
        </div>
        """, unsafe_allow_html=True)

    st.divider()

    # ── 광고 문구 출력 ──────────────────────────────────────────────
    st.markdown("### ✍️ 광고 문구 5개")
    for i, copy in enumerate(st.session_state.ad_copies or [], 1):
        st.markdown(f"""
        <div class="ad-card">
            <b>#{i} {copy.get('headline', '')}</b><br>
            <div class="copy-text">{copy.get('body', '')}</div>
            <small>👉 CTA: <b>{copy.get('cta', '')}</b> &nbsp;|&nbsp; 말투: {copy.get('tone', '')}</small>
        </div>
        """, unsafe_allow_html=True)

    st.divider()

    # ── 광고 소재 기획 출력 ─────────────────────────────────────────
    st.markdown("### 🎬 광고 소재 기획 5개")
    for i, concept in enumerate(st.session_state.ad_concepts or [], 1):
        with st.expander(f"소재 {i}. {concept.get('concept', '')} [{concept.get('format', '')}]"):
            c1, c2 = st.columns(2)
            with c1:
                st.markdown(f"**📋 시나리오**\n\n{concept.get('scenario', '')}")
                st.markdown(f"**🎯 타겟 모먼트**\n\n{concept.get('target_moment', '')}")
            with c2:
                st.markdown(f"**🖼️ 핵심 비주얼**\n\n{concept.get('key_visual', '')}")

    st.divider()

    # ── 전체 결과 다운로드 ──────────────────────────────────────────
    st.markdown("### 📥 결과 다운로드")
    output = {
        "product": st.session_state.product_data.get("product_name", ""),
        "usp": st.session_state.usp_result,
        "ad_copies": st.session_state.ad_copies,
        "ad_concepts": st.session_state.ad_concepts,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    st.download_button(
        "📄 JSON으로 다운로드",
        data=json.dumps(output, ensure_ascii=False, indent=2),
        file_name=f"ad_creative_{datetime.now().strftime('%Y%m%d_%H%M')}.json",
        mime="application/json",
    )
