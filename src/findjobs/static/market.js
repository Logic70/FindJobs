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
    const cloudLayoutFactory = window.d3?.layout?.cloud;
    let selectedId = keywords.length ? String(keywords[0].id) : null;
    let selectedDimension = "company";
    let activeCloudLayout = null;
    let cloudLayoutFrame = null;
    let cloudLayoutGeneration = 0;
    let lastCloudWidth = cloud?.clientWidth || 0;

    const percent = (value) => `${(Number(value || 0) * 100).toFixed(1)}%`;

    const text = (tag, value, className) => {
        const element = document.createElement(tag);
        element.textContent = String(value);
        if (className) element.className = className;
        return element;
    };

    const cloudButtons = () =>
        Array.from(cloud?.querySelectorAll("[data-keyword-id]") || []);

    const computeKeywordFontSize = (jobCount, allCounts, maximum = 40) => {
        const MIN = 13;
        const valid = allCounts
            .map((value) => Number(value))
            .filter((value) => Number.isFinite(value) && value > 0);
        if (valid.length === 0) return MIN;

        const count = Number(jobCount);
        if (!Number.isFinite(count) || count <= 0) return MIN;

        const sqrtValues = valid.map((value) => Math.sqrt(value));
        const lo = Math.min(...sqrtValues);
        const hi = Math.max(...sqrtValues);

        if (hi === lo) {
            return Math.round((MIN + maximum) / 2);
        }

        const ratio = Math.max(
            0,
            Math.min(1, (Math.sqrt(count) - lo) / (hi - lo))
        );
        return Math.round(MIN + ratio * (maximum - MIN));
    };

    const stableHash = (value) => {
        let hash = 2166136261;
        for (const character of String(value)) {
            hash ^= character.codePointAt(0);
            hash = Math.imul(hash, 16777619);
        }
        return hash >>> 0;
    };

    const seededRandom = (seed) => {
        let state = stableHash(seed);
        return () => {
            state += 0x6d2b79f5;
            let value = state;
            value = Math.imul(value ^ (value >>> 15), value | 1);
            value ^= value + Math.imul(value ^ (value >>> 7), value | 61);
            return ((value ^ (value >>> 14)) >>> 0) / 4294967296;
        };
    };

    const keywordRotation = (item, index) => {
        if (index < 10) return 0;
        const hash = stableHash(item.id);
        if (hash % 8 !== 0) return 0;
        return hash % 2 === 0 ? 90 : -90;
    };

    const keywordTextUnits = (value) =>
        Array.from(String(value)).reduce(
            (total, character) =>
                total + (character.codePointAt(0) <= 0x00ff ? 0.62 : 1),
            0
        );

    const cloudHeight = (words, width, attempt) => {
        const estimatedArea = words.reduce((total, word) => {
            const textWidth = Math.max(
                word.size * 1.5,
                keywordTextUnits(word.text) * word.size
            );
            return total + textWidth * word.size * 1.55;
        }, 0);
        const minimum = width <= 520 ? 820 : 340;
        const densityHeight = estimatedArea / Math.max(1, width * 0.46);
        return Math.ceil(Math.max(minimum, densityHeight) * (1 + attempt * 0.35));
    };

    const cloudWords = (buttons) => {
        const counts = keywords.map((item) => Number(item.job_count));
        const maximum = window.matchMedia("(max-width: 560px)").matches ? 32 : 40;
        return buttons.map((button, index) => {
            const item = byId.get(button.dataset.keywordId);
            const size = computeKeywordFontSize(item?.job_count, counts, maximum);
            button.style.setProperty(
                "--keyword-font-size",
                `${size}px`
            );
            return {
                id: String(item.id),
                text: String(item.name),
                size,
                rotate: keywordRotation(item, index),
            };
        });
    };

    const clearCloudPositioning = () => {
        cloud?.classList.remove("is-positioned", "is-laying-out");
        cloud?.style.removeProperty("height");
        cloud?.setAttribute("aria-busy", "false");
        if (cloud) cloud.dataset.layout = "fallback";
        cloudButtons().forEach((button) => {
            button.style.removeProperty("left");
            button.style.removeProperty("top");
            button.style.removeProperty("transform");
            delete button.dataset.cloudRotation;
        });
    };

    const applyCloudPlacement = (placed, width, height, inset, visibleIds) => {
        const placedById = new Map(placed.map((word) => [word.id, word]));
        const visibleWords = placed.filter((word) => visibleIds.has(word.id));
        const bounds = visibleWords.reduce(
            (result, word) => ({
                left: Math.min(result.left, word.x + word.x0),
                right: Math.max(result.right, word.x + word.x1),
                top: Math.min(result.top, word.y + word.y0),
                bottom: Math.max(result.bottom, word.y + word.y1),
            }),
            {
                left: Infinity,
                right: -Infinity,
                top: Infinity,
                bottom: -Infinity,
            }
        );
        const centerX = (bounds.left + bounds.right) / 2;
        const centerY = (bounds.top + bounds.bottom) / 2;
        const visibleHeight = Math.max(160, bounds.bottom - bounds.top + inset * 2);
        const renderedHeight = Math.min(height, Math.ceil(visibleHeight));
        cloudButtons().forEach((button) => {
            const word = placedById.get(button.dataset.keywordId);
            if (!word) return;
            button.style.left = `${inset + width / 2 + word.x - centerX}px`;
            button.style.top = `${inset + renderedHeight / 2 + word.y - centerY}px`;
            button.style.transform =
                `translate(-50%, -50%) rotate(${word.rotate}deg)`;
            button.dataset.cloudRotation = String(word.rotate);
        });
        cloud.style.height = `${renderedHeight + inset * 2}px`;
        cloud.classList.add("is-positioned");
        cloud.classList.remove("is-laying-out");
        cloud.setAttribute("aria-busy", "false");
        cloud.dataset.layout = "cloud";
        cloud.dataset.placedCount = String(visibleWords.length);
        cloud.dataset.layoutWordCount = String(placed.length);
    };

    const runCloudLayout = (
        words,
        width,
        inset,
        generation,
        visibleIds,
        attempt = 0
    ) => {
        const MAX_ATTEMPTS = 4;
        const height = cloudHeight(words, width, attempt);
        const layoutWords = words.map((word) => ({ ...word }));
        const seed = `${width}:${height}:${words.map((word) => word.id).join("|")}`;
        activeCloudLayout = cloudLayoutFactory()
            .size([width, height])
            .words(layoutWords)
            .text((word) => word.text)
            .font("Segoe UI")
            .fontSize((word) => word.size)
            .fontWeight(500)
            .rotate((word) => word.rotate)
            .padding(width <= 520 ? 12 : 10)
            .spiral("archimedean")
            .random(seededRandom(seed))
            .timeInterval(16)
            .on("end", (placed) => {
                if (generation !== cloudLayoutGeneration) return;
                cloud.dataset.lastAttempt = String(attempt + 1);
                cloud.dataset.lastPlacedCount = String(placed.length);
                const placedIds = new Set(placed.map((word) => word.id));
                cloud.dataset.lastMissing = words
                    .filter((word) => !placedIds.has(word.id))
                    .map((word) => word.id)
                    .join(",");
                if (placed.length !== words.length) {
                    if (attempt + 1 < MAX_ATTEMPTS) {
                        runCloudLayout(
                            words,
                            width,
                            inset,
                            generation,
                            visibleIds,
                            attempt + 1
                        );
                    } else {
                        clearCloudPositioning();
                    }
                    return;
                }
                applyCloudPlacement(placed, width, height, inset, visibleIds);
            });
        activeCloudLayout.start();
    };

    const layoutCloud = () => {
        if (!cloud) return;
        cloudLayoutGeneration += 1;
        const generation = cloudLayoutGeneration;
        activeCloudLayout?.stop();
        activeCloudLayout = null;

        const allButtons = cloudButtons();
        const words = cloudWords(allButtons);
        const visibleButtons = allButtons.filter((button) => !button.hidden);
        const visibleIds = new Set(
            visibleButtons.map((button) => button.dataset.keywordId)
        );
        if (!visibleIds.size || typeof cloudLayoutFactory !== "function") {
            clearCloudPositioning();
            return;
        }

        const inset = window.matchMedia("(max-width: 560px)").matches ? 8 : 12;
        const width = Math.floor(cloud.clientWidth - inset * 2);
        if (width < 100) {
            clearCloudPositioning();
            return;
        }
        cloud.classList.add("is-laying-out");
        cloud.setAttribute("aria-busy", "true");
        runCloudLayout(words, width, inset, generation, visibleIds);
    };

    const scheduleCloudLayout = () => {
        if (cloudLayoutFrame !== null) cancelAnimationFrame(cloudLayoutFrame);
        cloudLayoutFrame = requestAnimationFrame(() => {
            cloudLayoutFrame = null;
            layoutCloud();
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
        scheduleCloudLayout();
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
    window.addEventListener("resize", () => {
        const currentWidth = cloud?.clientWidth || 0;
        if (Math.abs(currentWidth - lastCloudWidth) < 2) return;
        lastCloudWidth = currentWidth;
        scheduleCloudLayout();
    });
    scheduleCloudLayout();
    if (selectedId) selectKeyword(selectedId);
})();
