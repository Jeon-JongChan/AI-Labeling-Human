/**
 * local-rag 라벨링 도구 — Vue 3
 */

const { createApp, ref, computed, onMounted, onBeforeUnmount } = Vue;

createApp({
  setup() {
    const docs = ref([]);
    const currentDocId = ref(null);
    const items = ref([]);
    const busy = ref(false);

    const useOcr = ref(true);
    const extracting = ref(false);
    const extractError = ref(null);
    const progress = ref({ stage: "", done: 0, total: 0 });
    let pollTimer = null;

    const batchRunning = ref(false);
    const batchStatus = ref({ stage: "", current: 0, total: 0, current_doc: "" });
    let batchTimer = null;

    const batchPercent = computed(() => {
      const s = batchStatus.value;
      if (!s.total) return 0;
      return Math.round((s.current / s.total) * 100);
    });

    const statusFilter = ref("all");
    const kindFilter = ref("all");
    const zoomImage = ref(null);

    const statusFilters = [
      { value: "all", label: "전체" },
      { value: "pending", label: "미검토" },
      { value: "approved", label: "적합" },
      { value: "rejected", label: "부적합" },
    ];
    const kindFilters = [
      { value: "all", label: "모든 유형" },
      { value: "text", label: "텍스트" },
      { value: "table", label: "표" },
      { value: "image", label: "그림" },
    ];

    const currentDoc = computed(() =>
      docs.value.find((d) => d.id === currentDocId.value) || null
    );

    const filteredItems = computed(() =>
      items.value.filter((it) => {
        if (statusFilter.value !== "all" && it.status !== statusFilter.value) return false;
        if (kindFilter.value !== "all" && it.kind !== kindFilter.value) return false;
        return true;
      })
    );

    const progressLabel = computed(() => {
      const p = progress.value;
      if (!p.stage) return "추출 준비 중…";
      if (p.total > 0) return `${p.stage} — ${p.done}/${p.total}`;
      return p.stage;
    });

    function statusCount(status) {
      if (status === "all") return items.value.length;
      return items.value.filter((it) => it.status === status).length;
    }

    function kindLabel(kind) {
      return { text: "텍스트", table: "표", image: "그림" }[kind] || kind;
    }
    function statusLabel(status) {
      return { pending: "미검토", approved: "적합", rejected: "부적합" }[status] || status;
    }

    // 원본 텍스트: 추출 텍스트가 없으면 OCR 결과 (그림 항목)
    function originalText(item) {
      return item.text || item.ocr_text || "";
    }

    function displayText(item) {
      if (item.edited_text !== null && item.edited_text !== undefined) {
        return item.edited_text;
      }
      return originalText(item);
    }

    function isEdited(item) {
      return (
        item.edited_text !== null &&
        item.edited_text !== undefined &&
        item.edited_text !== originalText(item)
      );
    }

    async function api(method, path, body) {
      const opts = { method, headers: { "Content-Type": "application/json" } };
      if (body !== undefined) opts.body = JSON.stringify(body);
      const res = await fetch(path, opts);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      return data;
    }

    async function loadDocs() {
      const data = await api("GET", "/api/docs");
      docs.value = data.docs || [];
    }

    async function scanDocs() {
      busy.value = true;
      try {
        const data = await api("POST", "/api/docs/scan");
        docs.value = data.docs || [];
      } catch (err) {
        alert("재탐색 실패: " + err.message);
      } finally {
        busy.value = false;
      }
    }

    let batchPollTicks = 0;
    async function pollBatchStatus() {
      try {
        const data = await api("GET", "/api/batch/status");
        batchRunning.value = !!data.running;
        batchStatus.value = data;
        batchPollTicks++;
        if (data.running && batchPollTicks % 2 === 0) {
          await loadDocs();
        }
        if (!data.running && batchTimer) {
          clearInterval(batchTimer);
          batchTimer = null;
          await loadDocs();
          if (currentDocId.value) await loadItems();
        }
      } catch (e) {
        console.error("배치 상태 조회 오류", e);
      }
    }

    async function startBatch(mode = "vlm") {
      if (batchRunning.value) return;
      const isVlmMode = mode === "vlm";
      const confirmMsg = isVlmMode
        ? "VLM 우선 전체 분석을 시작하시겠습니까?\n- 시간 소모가 큰 EasyOCR을 생략하고 고속으로 구조와 이미지를 크롭한 뒤 Gemini VLM 설명으로 직행합니다.\n- 이미 완료된 표/그림은 스킵하여 API가 중복 소모되지 않습니다."
        : "전체 문서 일괄 분석을 시작하시겠습니까?\n- EasyOCR과 Gemini VLM을 모두 수행합니다.\n- 이미 추출된 문서는 건너뜁니다.";
      if (!confirm(confirmMsg)) return;
      try {
        const query = isVlmMode ? "?ocr=0&vlm=1" : "?ocr=1&vlm=1";
        await api("POST", `/api/batch/start${query}`);
        batchRunning.value = true;
        batchStatus.value = {
          mode,
          stage: isVlmMode ? "VLM 우선 분석 시작 중..." : "일괄 분석 시작 중...",
          current: 0,
          total: docs.value.length,
        };
        if (batchTimer) clearInterval(batchTimer);
        batchTimer = setInterval(pollBatchStatus, 1500);
      } catch (err) {
        alert("분석 시작 실패: " + err.message);
      }
    }

    async function stopBatch() {
      if (!confirm("진행 중인 일괄 분석을 중단하시겠습니까?")) return;
      try {
        await api("POST", "/api/batch/stop");
        batchStatus.value.stage = "중단 요청됨...";
      } catch (err) {
        alert("중단 실패: " + err.message);
      }
    }

    async function loadItems() {
      if (!currentDocId.value) return;
      const data = await api("GET", `/api/docs/${currentDocId.value}/items`);
      items.value = data.items || [];
    }

    async function selectDoc(id) {
      currentDocId.value = id;
      statusFilter.value = "all";
      kindFilter.value = "all";
      extractError.value = null;
      stopPolling();
      extracting.value = false;
      await loadItems();
      // 추출이 진행 중이던 문서면 폴링 재개
      const p = await api("GET", `/api/docs/${id}/progress`);
      if (p.running) {
        extracting.value = true;
        startPolling();
      }
    }

    async function startExtract() {
      if (!currentDocId.value) return;
      if (items.value.length) {
        const ok = confirm(
          "다시 추출하면 이 문서의 기존 라벨이 모두 삭제됩니다. 계속할까요?"
        );
        if (!ok) return;
      }
      extractError.value = null;
      try {
        await api(
          "POST",
          `/api/docs/${currentDocId.value}/extract?ocr=${useOcr.value ? 1 : 0}`
        );
        extracting.value = true;
        progress.value = { stage: "대기", done: 0, total: 0 };
        startPolling();
      } catch (err) {
        extractError.value = err.message;
      }
    }

    function startPolling() {
      stopPolling();
      pollTimer = setInterval(async () => {
        if (!currentDocId.value) return stopPolling();
        try {
          const p = await api("GET", `/api/docs/${currentDocId.value}/progress`);
          progress.value = p;
          if (!p.running) {
            stopPolling();
            extracting.value = false;
            if (p.error) {
              extractError.value = p.error;
            } else {
              await Promise.all([loadItems(), loadDocs()]);
            }
          }
        } catch {
          /* 다음 폴링에서 재시도 */
        }
      }, 1000);
    }

    function stopPolling() {
      if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
      }
    }

    async function setStatus(item, status) {
      const prev = item.status;
      item.status = status;
      try {
        await api("PATCH", `/api/items/${item.id}`, { status });
        await loadDocs(); // 사이드바 집계 갱신
      } catch (err) {
        item.status = prev;
        alert("저장 실패: " + err.message);
      }
    }

    async function saveText(item, value) {
      const prev = item.edited_text;
      item.edited_text = value;
      try {
        await api("PATCH", `/api/items/${item.id}`, { edited_text: value });
      } catch (err) {
        item.edited_text = prev;
        alert("저장 실패: " + err.message);
      }
    }

    // VLM 캡션 생성 중인 항목 id 목록
    const captioning = ref([]);

    function isCaptioning(item) {
      return captioning.value.includes(item.id);
    }

    async function generateCaption(item) {
      if (isCaptioning(item)) return;
      captioning.value.push(item.id);
      try {
        const data = await api("POST", `/api/items/${item.id}/caption`);
        await saveText(item, data.caption);
      } catch (err) {
        alert("VLM 설명 생성 실패: " + err.message);
      } finally {
        captioning.value = captioning.value.filter((id) => id !== item.id);
      }
    }

    async function resetText(item) {
      try {
        await api("PATCH", `/api/items/${item.id}`, { edited_text: originalText(item) });
        await loadItems();
      } catch (err) {
        alert("되돌리기 실패: " + err.message);
      }
    }

    function exportUrl(format, scope) {
      return `/api/docs/${currentDocId.value}/export?format=${format}&scope=${scope}`;
    }

    async function importJsonl(ev) {
      const file = ev.target.files && ev.target.files[0];
      ev.target.value = "";
      if (!file || !currentDocId.value) return;
      busy.value = true;
      try {
        const jsonl = await file.text();
        const data = await api("POST", `/api/docs/${currentDocId.value}/import`, {
          jsonl,
        });
        await Promise.all([loadItems(), loadDocs()]);
        const parts = [`반영 ${data.updated}개`];
        if (data.unmatched) parts.push(`못 찾음 ${data.unmatched}개`);
        if (data.skipped) parts.push(`변경 없음 ${data.skipped}개`);
        alert(parts.join(" · "));
      } catch (err) {
        alert("JSONL 반영 실패: " + err.message);
      } finally {
        busy.value = false;
      }
    }

    onMounted(async () => {
      await loadDocs();
      await pollBatchStatus();
      if (batchRunning.value) {
        batchTimer = setInterval(pollBatchStatus, 1500);
      }
    });
    onBeforeUnmount(() => {
      stopPolling();
      if (batchTimer) clearInterval(batchTimer);
    });

    return {
      docs,
      currentDocId,
      currentDoc,
      items,
      filteredItems,
      busy,
      batchRunning,
      batchStatus,
      batchPercent,
      startBatch,
      stopBatch,
      useOcr,
      extracting,
      extractError,
      progressLabel,
      statusFilter,
      kindFilter,
      statusFilters,
      kindFilters,
      zoomImage,
      statusCount,
      kindLabel,
      statusLabel,
      displayText,
      isEdited,
      scanDocs,
      selectDoc,
      startExtract,
      setStatus,
      saveText,
      resetText,
      isCaptioning,
      generateCaption,
      exportUrl,
      importJsonl,
    };
  },
}).mount("#app");
