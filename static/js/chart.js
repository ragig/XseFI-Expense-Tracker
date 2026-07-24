document.querySelectorAll(".chart-canvas").forEach((chart) => {
    const labels = JSON.parse(chart.dataset.labels || "[]");
    const values = JSON.parse(chart.dataset.values || "[]");
    const ctx = chart.getContext("2d");
    const colors = ["#10f708", "#84cc16", "#16a34a", "#a3e635", "#65a30d", "#f59e0b", "#dc2626", "#4d7c0f"];
    const chartHeight = Number(chart.dataset.height || chart.getAttribute("height") || 240);
    const fitLabel = (label, maxWidth) => {
        let text = String(label);

        if (ctx.measureText(text).width <= maxWidth) {
            return text;
        }

        while (text.length > 1 && ctx.measureText(`${text}...`).width > maxWidth) {
            text = text.slice(0, -1);
        }

        return `${text}...`;
    };
    const labelLines = (label) => {
        const text = String(label || "");

        if (text.includes(" to ")) {
            const [from, to] = text.split(" to ");
            return [from, `to ${to}`];
        }

        return [text];
    };

    const drawChart = () => {
        const rect = chart.getBoundingClientRect();
        const ratio = window.devicePixelRatio || 1;
        chart.width = rect.width * ratio;
        chart.height = chartHeight * ratio;
        ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
        ctx.clearRect(0, 0, rect.width, chartHeight);

        const max = Math.max(...values, 1);
        const gap = values.length > 12 ? 6 : 12;
        const padding = 32;
        const hasRangeLabels = labels.some((label) => String(label).includes(" to "));
        const labelSpace = hasRangeLabels ? 62 : 48;
        const valueSpace = 28;
        const barArea = rect.width - padding * 2;
        const barWidth = Math.max(6, Math.min(46, (barArea - gap * (values.length - 1)) / values.length));
        const plotHeight = chartHeight - labelSpace - valueSpace - 18;
        const baseline = chartHeight - labelSpace;

        ctx.font = "12px Arial";
        ctx.textBaseline = "middle";
        ctx.strokeStyle = "#dfe9d3";
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(padding, baseline + 0.5);
        ctx.lineTo(rect.width - padding, baseline + 0.5);
        ctx.stroke();

        values.forEach((value, index) => {
            const x = padding + index * (barWidth + gap);
            const height = Math.max(6, (value / max) * plotHeight);
            const y = baseline - height;

            ctx.fillStyle = colors[index % colors.length];
            if (typeof ctx.roundRect === "function") {
                ctx.beginPath();
                ctx.roundRect(x, y, barWidth, height, 4);
                ctx.fill();
            } else {
                ctx.fillRect(x, y, barWidth, height);
            }

            ctx.fillStyle = "#17230c";
            ctx.textAlign = "left";
            ctx.fillText(`Rs. ${value.toFixed(0)}`, x, Math.max(12, y - 12));

            ctx.fillStyle = "#6b7f55";
            ctx.textAlign = "center";
            labelLines(labels[index]).forEach((line, lineIndex) => {
                ctx.fillText(
                    fitLabel(line, barWidth + gap),
                    x + barWidth / 2,
                    baseline + 22 + lineIndex * 15
                );
            });
        });
    };

    drawChart();
    window.addEventListener("resize", drawChart);
});
