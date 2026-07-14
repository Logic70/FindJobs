"use strict";

(() => {
    const dataElement = document.getElementById("market-keyword-data");
    if (!dataElement) return;

    let analysis;
    try {
        analysis = JSON.parse(dataElement.textContent);
    } catch (_error) {
        return;
    }

    const keywords = Array.isArray(analysis.keywords) ? analysis.keywords : [];
    const byId = new Map(keywords.map((item) => [String(item.id), item]));
    const cloud = document.getElementById("keyword-cloud");
    const detail = document.getElementById("keyword-detail");
    const detailName = document.getElementById("keyword-detail-name");
    const detailKind = document.getElementById("keyword-detail-kind");
    const metrics = document.getElementById("keyword-metrics");
    const distribution = document.getElementById("keyword-distribution");
    const related = document.getElementById("related-keywords");
    const examples = document.getElementById("keyword-examples");
    const kindFilter = document.getElementById("keyword-kind");
    const search = document.getElementById("keyword-search");
    const dimensionButtons = Array.from(
        document.querySelectorAll("[data-dimension]")
    );
    let selectedId = keywords.length ? String(keywords[0].id) : null;
    let selectedDimension = "company";

    const percent = (value) => `${(Number(value || 0) * 100).toFixed(1)}%`;

    const text = (tag, value, className) => {
        const element = document.createElement(tag);
        element.textContent = String(value);
        if (className) element.className = className;
        return element;
    };

    const cloudButtons = () =>
        Array.from(cloud?.querySelectorAll("[data-keyword-id]") || []);

    const computeKeywordFontSize = (jobCount, allCounts) => {
        const MIN = 13;
        const MAX = 40;
        const valid = allCounts
            .map((value) => Number(value))
            .filter((value) => Number.isFinite(value) && value > 0);
        if (valid.length === 0) return `${MIN}px`;

        const count = Number(jobCount);
        if (!Number.isFinite(count) || count <= 0) return `${MIN}px`;

        const sqrtValues = valid.map((value) => Math.sqrt(value));
        const lo = Math.min(...sqrtValues);
        const hi = Math.max(...sqrtValues);

        if (hi === lo) {
            const mid = Math.round((MIN + MAX) / 2);
            return `${mid}px`;
        }

        const ratio = Math.max(0, Math.min(1, (Math.sqrt(count) - lo) / (hi - lo)));
        return `${Math.round(MIN + ratio * (MAX - MIN))}px`;
    };

    const setCloudSizes = () => {
        const counts = keywords.map((item) => Number(item.job_count));
        cloudButtons().forEach((button) => {
            const item = byId.get(button.dataset.keywordId);
            button.style.setProperty(
                "--keyword-font-size",
                computeKeywordFontSize(item?.job_count, counts)
            );
        });
    };

    const renderMetrics = (item) => {
        metrics.replaceChildren();
        const values = [
            [`${item.job_count}/${item.job_denominator}`, "岗位"],
            [percent(item.job_coverage), "覆盖率"],
            [item.company_count, "家公司"],
            [item.work_content_job_count, "职责提及"],
        ];
        values.forEach(([value, label]) => {
            const span = document.createElement("span");
            span.append(text("strong", value), document.createTextNode(` ${label}`));
            metrics.append(span);
        });
    };

    const renderDistribution = (item) => {
        distribution.replaceChildren();
        const rows = item.distributions?.[selectedDimension] || [];
        if (!rows.length) {
            distribution.append(text("p", "无分布数据", "empty"));
            return;
        }
        rows.forEach((row) => {
            const wrapper = text("div", "", "distribution-row");
            const label = text("div", "", "distribution-label");
            label.append(text("span", row.name), text("strong", row.job_count));
            const track = text("div", "", "distribution-track");
            const bar = document.createElement("span");
            bar.style.width = `${Math.max(
                0,
                Math.min(100, Number(row.share_of_keyword || 0) * 100)
            )}%`;
            track.append(bar);
            const meta = text(
                "div",
                `关键词占比 ${percent(row.share_of_keyword)} · 组内覆盖 ${percent(
                    row.group_coverage
                )}`,
                "distribution-meta"
            );
            wrapper.append(label, track, meta);
            distribution.append(wrapper);
        });
    };

    const renderRelated = (item) => {
        related.replaceChildren();
        const rows = item.related_keywords || [];
        if (!rows.length) {
            related.append(text("span", "无", "empty"));
            return;
        }
        rows.forEach((row) => {
            const button = text("button", `${row.name} · ${row.job_count}`);
            button.type = "button";
            button.dataset.relatedId = String(row.id);
            button.addEventListener("click", () => selectKeyword(String(row.id)));
            related.append(button);
        });
    };

    const renderExamples = (item) => {
        examples.replaceChildren();
        const rows = item.example_jobs || [];
        if (!rows.length) {
            examples.append(text("li", "无", "empty"));
            return;
        }
        rows.forEach((row) => {
            const itemElement = document.createElement("li");
            const link = text("a", `${row.company_name} · ${row.title}`);
            link.href = `/jobs/${encodeURIComponent(String(row.job_id))}`;
            itemElement.append(link);
            if (row.locations?.length) {
                itemElement.append(document.createTextNode(` · ${row.locations.join("、")}`));
            }
            examples.append(itemElement);
        });
    };

    const selectKeyword = (keywordId) => {
        const item = byId.get(keywordId);
        if (!item) return;
        selectedId = keywordId;
        detail.hidden = false;
        detailName.textContent = item.name;
        detailKind.textContent = item.category;
        detailKind.dataset.kind = item.kind;
        cloudButtons().forEach((button) => {
            const active = button.dataset.keywordId === keywordId;
            button.classList.toggle("is-active", active);
            button.setAttribute("aria-pressed", String(active));
        });
        renderMetrics(item);
        renderDistribution(item);
        renderRelated(item);
        renderExamples(item);
    };

    const applyFilters = () => {
        const kind = kindFilter.value;
        const query = search.value.trim().toLocaleLowerCase("zh-CN");
        cloudButtons().forEach((button) => {
            const item = byId.get(button.dataset.keywordId);
            const visible =
                item &&
                (kind === "all" || item.kind === kind) &&
                (!query || String(item.name).toLocaleLowerCase("zh-CN").includes(query));
            button.hidden = !visible;
        });
        const selectedButton = cloud?.querySelector(
            `[data-keyword-id="${CSS.escape(selectedId || "")}"]`
        );
        if (selectedButton?.hidden) {
            const next = cloudButtons().find((button) => !button.hidden);
            if (next) selectKeyword(next.dataset.keywordId);
            else detail.hidden = true;
        }
    };

    dimensionButtons.forEach((button) => {
        button.addEventListener("click", () => {
            selectedDimension = button.dataset.dimension;
            dimensionButtons.forEach((candidate) =>
                candidate.setAttribute(
                    "aria-selected",
                    String(candidate === button)
                )
            );
            const item = byId.get(selectedId);
            if (item) renderDistribution(item);
        });
    });
    cloudButtons().forEach((button) =>
        button.addEventListener("click", () =>
            selectKeyword(button.dataset.keywordId)
        )
    );
    kindFilter?.addEventListener("change", applyFilters);
    search?.addEventListener("input", applyFilters);
    setCloudSizes();
    if (selectedId) selectKeyword(selectedId);
})();
