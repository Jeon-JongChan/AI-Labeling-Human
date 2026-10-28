"""
PDF 시각 요소(그림/표) 정제 및 무의미하게 쪼개진 조각 이미지 필터링 모듈.

- 풀페이지 배경 템플릿 이미지 제거
- 얇은 선/바/구분선 조각 제거
- 낱개 클립아트 및 초소형 아이콘 제거
- 상위 다이어그램/표 내부에 쪼개져 포함된 서브 컴포넌트(Containment) 중복 제거
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence, Tuple


def parse_bbox_coords(bbox: Any) -> Optional[Tuple[float, float, float, float]]:
    """
    다양한 형식(JSON 문자열, 리스트, 튜플)의 바운딩 박스를 [x0, y0, x1, y1] 실수 튜플로 정규화.
    """
    if bbox is None:
        return None
    try:
        if isinstance(bbox, str):
            bbox = json.loads(bbox)
        if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
            x0 = float(min(bbox[0], bbox[2]))
            y0 = float(min(bbox[1], bbox[3]))
            x1 = float(max(bbox[0], bbox[2]))
            y1 = float(max(bbox[1], bbox[3]))
            return (x0, y0, x1, y1)
    except Exception:
        pass
    return None


def calculate_box_area(box: Tuple[float, float, float, float]) -> float:
    """바운딩 박스의 면적(가로 x 세로, pt^2)을 계산."""
    w = max(0.0, box[2] - box[0])
    h = max(0.0, box[3] - box[1])
    return w * h


def calculate_intersection_area(
    box1: Tuple[float, float, float, float],
    box2: Tuple[float, float, float, float],
) -> float:
    """두 바운딩 박스의 교집합 면적을 계산."""
    x0 = max(box1[0], box2[0])
    y0 = max(box1[1], box2[1])
    x1 = min(box1[2], box2[2])
    y1 = min(box1[3], box2[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return (x1 - x0) * (y1 - y0)


def is_fragmented_or_meaningless_image(
    box: Tuple[float, float, float, float],
    page_w: float = 842.0,
    page_h: float = 595.0,
    min_dim_pt: float = 30.0,
    min_icon_pt: float = 60.0,
    min_area_pt: float = 3600.0,
) -> bool:
    """
    단일 이미지 객체가 의미 없는 슬라이스/아이콘/배경인지 단독 판별.

    Args:
        box: (x0, y0, x1, y1) 바운딩 박스
        page_w: 페이지 너비 (pt)
        page_h: 페이지 높이 (pt)
        min_dim_pt: 최소 허용 두께 (가로 또는 세로 중 최소값)
        min_icon_pt: 단독 아이콘 판별 기준 크기 (가로/세로 모두 이 값 미만이면 제외)
        min_area_pt: 최소 유효 도면 면적 (pt^2)

    Returns:
        bool: 의미 없는 조각/배경/노이즈이면 True, 유효하면 False
    """
    w = box[2] - box[0]
    h = box[3] - box[1]
    area = w * h

    # 1. 풀페이지 템플릿 배경 (페이지 너비/높이의 85% 이상 덮음)
    if page_w > 0 and page_h > 0:
        if w >= 0.85 * page_w and h >= 0.85 * page_h:
            return True

    # 2. 얇은 선/바/구분선/테두리 슬라이스 (한쪽 두께가 30pt 미만)
    if min(w, h) < min_dim_pt:
        return True

    # 3. 낱개 클립아트, 버튼, 아이콘 (가로/세로 모두 60pt 미만이거나 면적이 3,600pt^2 미만)
    if (w < min_icon_pt and h < min_icon_pt) or area < min_area_pt:
        return True

    return False


def filter_contained_sub_elements(
    elements: List[Dict[str, Any]],
    overlap_threshold: float = 0.70,
) -> List[Dict[str, Any]]:
    """
    동일 페이지 내에서 더 큰 상위 다이어그램/표 영역에 70% 이상 포함된
    하위 서브 조각(아이콘, 텍스트 조각 등)을 제거합니다.

    Args:
        elements: 동일 페이지에 속한 visual (image/table) 요소 목록
        overlap_threshold: 포함 판정 기준 비율 (기본 70%)

    Returns:
        List[Dict[str, Any]]: 중복 서브 요소가 제거된 정제된 요소 목록
    """
    if len(elements) <= 1:
        return list(elements)

    # 각 요소의 bbox 및 면적 파싱
    parsed_items = []
    for elem in elements:
        box = parse_bbox_coords(elem.get("bbox"))
        area = calculate_box_area(box) if box else 0.0
        parsed_items.append({"elem": elem, "box": box, "area": area})

    kept = []
    for i, it1 in enumerate(parsed_items):
        box1 = it1["box"]
        area1 = it1["area"]

        # 바운딩 박스가 없으면 그대로 유지
        if not box1 or area1 <= 0.0:
            kept.append(it1["elem"])
            continue

        is_sub_element = False
        for j, it2 in enumerate(parsed_items):
            if i == j:
                continue
            box2 = it2["box"]
            area2 = it2["area"]

            # it2가 더 큰 영역일 때만 it1이 it2의 부속인지 검사
            if area2 <= area1 or not box2:
                continue

            inter = calculate_intersection_area(box1, box2)
            # it1(작은 조각) 면적의 overlap_threshold 이상이 it2(큰 다이어그램) 안에 포함되는 경우
            if (inter / area1) >= overlap_threshold:
                is_sub_element = True
                break

        if not is_sub_element:
            kept.append(it1["elem"])

    return kept



def is_real_data_table(elem: Dict[str, Any]) -> bool:
    """
    텍스트 내용이 풍부하고 행/열 구조를 가진 실제 데이터 표인지 판별.
    다이어그램 내부의 도형 상자(가짜 표)와 구분하기 위해 사용됩니다.
    """
    if elem.get("kind") != "table":
        return False
    text = (elem.get("text") or "").strip()
    if len(text) < 20:
        return False
    # 셀 구분자(|)나 2개 이상의 개행(\n)이 있는 정형 데이터 표
    if "|" in text or text.count("\n") >= 2:
        return True
    return False


def bbox_distance(
    b1: Tuple[float, float, float, float],
    b2: Tuple[float, float, float, float],
) -> Tuple[float, float]:
    """두 바운딩 박스 사이의 축별 최소 거리 (dx, dy) 계산."""
    dx = max(0.0, max(b1[0] - b2[2], b2[0] - b1[2]))
    dy = max(0.0, max(b1[1] - b2[3], b2[1] - b1[3]))
    return dx, dy


def union_bboxes(
    bboxes: Sequence[Tuple[float, float, float, float]],
    pad: float = 10.0,
    page_w: float = 842.0,
    page_h: float = 595.0,
) -> Tuple[float, float, float, float]:
    """바운딩 박스 목록의 최소 포함 외접 사각형(Union BBox)을 계산하고 패딩 적용."""
    if not bboxes:
        return (0.0, 0.0, page_w, page_h)
    x0 = max(0.0, min(b[0] for b in bboxes) - pad)
    y0 = max(0.0, min(b[1] for b in bboxes) - pad)
    x1 = min(page_w, max(b[2] for b in bboxes) + pad)
    y1 = min(page_h, max(b[3] for b in bboxes) + pad)
    return (x0, y0, x1, y1)


def cluster_and_merge_diagrams(
    elements: List[Dict[str, Any]],
    page_w: float = 842.0,
    page_h: float = 595.0,
    dist_threshold: float = 50.0,
    is_slide_page: bool = False,
) -> List[Dict[str, Any]]:
    """
    동일 페이지 내에서 파편화된 다이어그램 조각(가짜 표, 화살표, 도형)들을 군집화하여
    온전한 단일/노드별 다이어그램으로 통합합니다.

    Args:
        elements: 동일 페이지에 속한 시각적 요소(table, image) 목록
        page_w: 페이지 너비
        page_h: 페이지 높이
        dist_threshold: 인접 판정 최대 거리 (pt)
        is_slide_page: 본문 텍스트가 적은 슬라이드형 구성도 페이지 여부

    Returns:
        List[Dict[str, Any]]: 조각들이 온전한 다이어그램으로 통합된 요소 목록
    """
    if len(elements) <= 1:
        return list(elements)

    real_tables: List[Dict[str, Any]] = []
    fragments: List[Dict[str, Any]] = []

    for el in elements:
        if is_real_data_table(el):
            real_tables.append(el)
        else:
            box = parse_bbox_coords(el.get("bbox"))
            if box:
                fragments.append({"elem": el, "box": box})
            else:
                real_tables.append(el)

    if not fragments:
        return real_tables

    if len(fragments) == 1:
        return real_tables + [fragments[0]["elem"]]

    # 1. 슬라이드형 구성도 페이지(조각 3개 이상): 모든 조각을 1개의 온전한 다이어그램으로 통합
    if is_slide_page and len(fragments) >= 3:
        all_boxes = [f["box"] for f in fragments]
        ubox = union_bboxes(all_boxes, pad=15.0, page_w=page_w, page_h=page_h)
        base_elem = fragments[0]["elem"]
        merged_elem = {
            "orig_idx": base_elem.get("orig_idx", 0),
            "element": base_elem.get("element"),
            "etype": "diagram",
            "kind": "image",
            "page": base_elem.get("page"),
            "bbox": list(ubox),
            "text": "",
            "is_merged_diagram": True,
        }
        return real_tables + [merged_elem]

    # 2. 일반 문서 내 다이어그램: 거리 기반 연결 군집화 (Connected Components)
    clusters: List[List[Dict[str, Any]]] = []
    for frag in fragments:
        matched_c = []
        for c_idx, cl in enumerate(clusters):
            for member in cl:
                dx, dy = bbox_distance(frag["box"], member["box"])
                if dx <= dist_threshold and dy <= dist_threshold:
                    matched_c.append(c_idx)
                    break
        if not matched_c:
            clusters.append([frag])
        elif len(matched_c) == 1:
            clusters[matched_c[0]].append(frag)
        else:
            new_cl = [frag]
            for c_idx in sorted(matched_c, reverse=True):
                new_cl.extend(clusters.pop(c_idx))
            clusters.append(new_cl)

    merged_results: List[Dict[str, Any]] = list(real_tables)
    for cl in clusters:
        if len(cl) == 1:
            merged_results.append(cl[0]["elem"])
        else:
            boxes = [m["box"] for m in cl]
            ubox = union_bboxes(boxes, pad=10.0, page_w=page_w, page_h=page_h)
            base_elem = cl[0]["elem"]
            merged_elem = {
                "orig_idx": base_elem.get("orig_idx", 0),
                "element": base_elem.get("element"),
                "etype": "diagram",
                "kind": "image",
                "page": base_elem.get("page"),
                "bbox": list(ubox),
                "text": "",
                "is_merged_diagram": True,
            }
            merged_results.append(merged_elem)

    return merged_results
