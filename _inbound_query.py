# -*- coding: utf-8 -*-
"""입고일자별 쿼리(공유). STL_NO=품번-컬러 키."""
QUERY = r"""
WITH gm AS (
  SELECT style_no, brand_nm FROM (
    SELECT style_no, brand_nm,
           ROW_NUMBER() OVER (PARTITION BY style_no ORDER BY style_no DESC) rn
    FROM datamart.datamart.goods WHERE com_id NOT IN ('musinsa_used','musinsa_event','musinsa')
  ) WHERE rn = 1
)
SELECT a.ACT_DATE AS dt,
       a.STL_NO AS sku_code,              -- STL_NO = 품번-컬러 (보드 SKU 키와 동일)
       MAX(gm.brand_nm) AS brd_nm,
       SUM(CAST(a.ACT_QTY AS BIGINT)) AS inbound_qty
FROM pbo.moms.ui_grreport_detail a
JOIN gm ON a.STL_NO = gm.style_no          -- goods.style_no 도 품번-컬러 (기존 입고현황과 동일 조인)
WHERE a.ORD_STATUS NOT IN ('출고취소','입고취소','입고대기')
  AND a.ORD_TYPE = '일반' AND a.SPR_NM = 'MUSINSA'
  AND gm.brand_nm IN ('무신사 스탠다드 우먼','무신사 스탠다드','무신사 스탠다드 홈',
                      '무신사 스탠다드 키즈','무신사 스탠다드 뷰티','무신사 스탠다드 스포츠','무신사스탠다드 지비지에이치')
  AND a.ACT_DATE >= '20251101'
  AND a.ACT_DATE <= DATE_FORMAT(DATE_SUB(CURRENT_DATE(), 1), 'yyyyMMdd')
GROUP BY a.ACT_DATE, a.STL_NO
ORDER BY dt, sku_code
"""
