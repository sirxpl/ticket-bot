/* Dependency-free charts. Missing measurements stay gaps, never zero samples. */
(() => {
    const source = document.getElementById('analyticsData');
    if (!source) return;
    const data = JSON.parse(source.textContent);
    const daily = data.daily;
    const ns = 'http://www.w3.org/2000/svg';
    function el(tag, attrs = {}, text) {
        const node = document.createElementNS(ns, tag);
        Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, value));
        if (text !== undefined) node.textContent = text;
        return node;
    }
    function duration(value) {
        if (value === null || value === undefined) return 'No data';
        if (value < 60) return `${Math.round(value)}s`;
        if (value < 3600) return `${(value / 60).toFixed(1)}m`;
        if (value < 86400) return `${(value / 3600).toFixed(1)}h`;
        return `${(value / 86400).toFixed(1)}d`;
    }
    // One viewport-level tooltip avoids native title delays and clipped card/heatmap content.
    const detailTip = document.createElement('div');
    detailTip.id = 'analyticsDetailTooltip';
    detailTip.className = 'analytics-tooltip analytics-detail-tooltip';
    detailTip.setAttribute('role', 'tooltip');
    detailTip.hidden = true;
    document.body.append(detailTip);
    let tipTarget = null;
    function hideDetail() {
        if (tipTarget) {
            const ids = (tipTarget.getAttribute('aria-describedby') || '').split(/\s+/).filter(id => id && id !== detailTip.id);
            if (ids.length) tipTarget.setAttribute('aria-describedby', ids.join(' '));
            else tipTarget.removeAttribute('aria-describedby');
        }
        tipTarget = null;
        detailTip.hidden = true;
    }
    function showDetail(target, pointer) {
        if (tipTarget !== target) hideDetail();
        tipTarget = target;
        detailTip.textContent = target.dataset.analyticsTooltip;
        detailTip.hidden = false;
        const ids = new Set((target.getAttribute('aria-describedby') || '').split(/\s+/).filter(Boolean));
        ids.add(detailTip.id);
        target.setAttribute('aria-describedby', [...ids].join(' '));
        const rect = target.getBoundingClientRect();
        const edge = 8, gap = 14;
        const anchorX = pointer ? pointer.clientX : rect.left + rect.width / 2;
        const anchorY = pointer ? pointer.clientY : rect.top;
        const left = Math.max(edge, Math.min(window.innerWidth-detailTip.offsetWidth-edge, anchorX+gap));
        let top = anchorY-detailTip.offsetHeight-gap;
        if (top < edge) top = pointer ? pointer.clientY+gap : rect.bottom+gap;
        top = Math.max(edge, Math.min(window.innerHeight-detailTip.offsetHeight-edge, top));
        detailTip.style.left = `${left}px`;
        detailTip.style.top = `${top}px`;
    }
    document.querySelectorAll('[data-analytics-tooltip]').forEach(target => {
        target.addEventListener('pointerenter', event => showDetail(target, event));
        target.addEventListener('pointermove', event => showDetail(target, event));
        target.addEventListener('pointerleave', hideDetail);
        target.addEventListener('focus', () => showDetail(target));
        target.addEventListener('blur', hideDetail);
        target.addEventListener('pointerdown', event => showDetail(target, event));
    });
    document.addEventListener('keydown', event => { if (event.key === 'Escape') hideDetail(); });
    document.addEventListener('pointerdown', event => {
        if (!event.target.closest('[data-analytics-tooltip]')) hideDetail();
    });
    function moveOrHideDetail() {
        // Keyboard focus may scroll a bucket/cell into view after its focus event.
        if (tipTarget && tipTarget === document.activeElement) showDetail(tipTarget);
        else hideDetail();
    }
    document.addEventListener('scroll', moveOrHideDetail, true);
    window.addEventListener('resize', moveOrHideDetail);
    document.querySelectorAll('[data-chart]').forEach(host => {
        const kind = host.dataset.chart;
        const series = kind === 'flow'
            ? [{key: 'created', label: 'Opened', color: '#a59afa'}, {key: 'closed', label: 'Closed', color: '#5ddeab'}]
            : [{key: kind, label: kind === 'reply' ? 'First reply' : 'Time to close', color: '#a59afa'}];
        const format = value => kind === 'flow' ? String(value) : duration(value);
        const baseline = data.baselines[kind];
        const values = daily.flatMap(day => series.map(s => day[s.key])).filter(v => v !== null);
        const max = Math.max(1, ...values, baseline || 0);
        const rawStep = max / 4;
        const magnitude = 10 ** Math.floor(Math.log10(rawStep));
        const step = [1, 2, 5, 10].find(n => n * magnitude >= rawStep) * magnitude;
        const ceiling = kind === 'flow' ? Math.max(4, Math.ceil(max / step) * step) : Math.ceil(max / step) * step;
        const W = 760, H = 255, L = 52, R = 18, T = 16, B = 32;
        const width = W - L - R, height = H - T - B;
        const x = i => L + i / Math.max(1, daily.length - 1) * width;
        const y = v => T + height - v / ceiling * height;
        const svg = el('svg', {viewBox: `0 0 ${W} ${H}`, 'aria-hidden': 'true'});
        host.append(svg);
        for (let i = 0; i <= 4; i++) {
            const value = ceiling * i / 4;
            svg.append(el('line', {x1:L, x2:W-R, y1:y(value), y2:y(value), stroke:'#a5b9ff16', 'stroke-dasharray':'3 5'}));
            svg.append(el('text', {x:L-9,y:y(value)+4,'text-anchor':'end'}, kind === 'flow' ? String(Math.round(value)) : duration(value)));
        }
        daily.forEach((day,i) => {
            if (i % Math.ceil(daily.length / 7) === 0 || i === daily.length - 1) {
                svg.append(el('text',{x:x(i),y:H-7,'text-anchor':'middle'}, day.date));
            }
        });
        if (baseline !== null && baseline !== undefined) {
            svg.append(el('line', {x1:L,x2:W-R,y1:y(baseline),y2:y(baseline),stroke:'#9ba5c4','stroke-dasharray':'5 5'}));
        }
        series.forEach(s => {
            let segment = [];
            function flush() {
                if (!segment.length) return;
                if (segment.length > 1) {
                    // Monotone cubic interpolation keeps the curve within each pair of samples.
                    // Unlike a generic spline it cannot imply negative ticket counts or overshoot.
                    const deltas = segment.slice(1).map((p,i) => (p[1]-segment[i][1])/(p[0]-segment[i][0]));
                    const slopes = segment.map((p,i) => {
                        if (i === 0) return deltas[0];
                        if (i === segment.length-1) return deltas.at(-1);
                        const a=deltas[i-1], b=deltas[i];
                        return a*b <= 0 ? 0 : 2*a*b/(a+b);
                    });
                    let path = `M ${segment[0][0]},${segment[0][1]}`;
                    for (let i=1;i<segment.length;i++) {
                        const a=segment[i-1], b=segment[i], dx=(b[0]-a[0])/3;
                        path += ` C ${a[0]+dx},${a[1]+slopes[i-1]*dx} ${b[0]-dx},${b[1]-slopes[i]*dx} ${b[0]},${b[1]}`;
                    }
                    svg.append(el('path',{d:`${path} L ${segment.at(-1)[0]},${y(0)} L ${segment[0][0]},${y(0)} Z`,fill:s.color,'fill-opacity':'.12',class:'analytics-area'}));
                    svg.append(el('path',{d:path,fill:'none',stroke:s.color,'stroke-width':'2','stroke-linejoin':'round',pathLength:'1',class:'analytics-series'}));
                }
                if (segment.length === 1) segment.forEach(([px,py]) => svg.append(el('circle',{cx:px,cy:py,r:3,fill:s.color})));
                segment = [];
            }
            daily.forEach((day,i) => {
                if (day[s.key] === null) flush();
                else segment.push([x(i), y(day[s.key])]);
            });
            flush();
        });
        if (!values.length) svg.append(el('text',{x:W/2,y:H/2,'text-anchor':'middle'},'No recorded timings in this period'));
        const guide = el('line',{x1:L,x2:L,y1:T,y2:H-B,stroke:'#b3bbdc50',visibility:'hidden'});
        svg.append(guide);
        const tooltip = document.createElement('div');
        tooltip.className = 'analytics-tooltip';
        tooltip.hidden = true;
        tooltip.setAttribute('role','status');
        host.append(tooltip);
        const markers = series.map(s => {
            const marker=el('circle',{r:4,fill:s.color,stroke:'#171b2b','stroke-width':2,visibility:'hidden'});
            svg.append(marker); return marker;
        });
        let selected = 0;
        function inspect(index, pointer) {
            selected = Math.max(0,Math.min(daily.length-1,index));
            const day = daily[selected];
            guide.setAttribute('x1',x(selected)); guide.setAttribute('x2',x(selected));
            guide.setAttribute('visibility','visible');
            tooltip.hidden = false;
            markers.forEach((marker,i) => {
                const value=day[series[i].key];
                marker.setAttribute('visibility',value === null ? 'hidden' : 'visible');
                if (value !== null) { marker.setAttribute('cx',x(selected)); marker.setAttribute('cy',y(value)); }
            });
            tooltip.textContent = [day.iso,...series.map(s => `${s.label}: ${format(day[s.key])}`),
                ...(baseline !== null && baseline !== undefined ? [`Previous ${kind === 'flow' ? 'opened ' : ''}average: ${kind === 'flow' ? baseline.toFixed(1) : duration(baseline)}`] : [])].join('\n');
            const rect=host.getBoundingClientRect();
            const left=pointer ? pointer.clientX-rect.left+16 : x(selected)/W*rect.width+12;
            const top=pointer ? pointer.clientY-rect.top-tooltip.offsetHeight-12 : 12;
            tooltip.style.left=`${Math.max(4,Math.min(rect.width-tooltip.offsetWidth-4,left))}px`;
            tooltip.style.top=`${Math.max(4,Math.min(rect.height-tooltip.offsetHeight-4,top))}px`;
        }
        function hide() { tooltip.hidden = true; guide.setAttribute('visibility','hidden'); markers.forEach(marker=>marker.setAttribute('visibility','hidden')); }
        function inspectPointer(event) {
            const point = svg.createSVGPoint(); point.x = event.clientX; point.y = event.clientY;
            const local = point.matrixTransform(svg.getScreenCTM().inverse());
            inspect(Math.round((local.x-L)/width*(daily.length-1)), event);
        }
        svg.addEventListener('pointerenter', inspectPointer);
        svg.addEventListener('pointermove', inspectPointer);
        host.addEventListener('pointerleave',hide);
        host.addEventListener('focus',() => inspect(selected));
        host.addEventListener('blur',hide);
        host.addEventListener('keydown',event => {
            if (!['ArrowLeft','ArrowRight','Escape'].includes(event.key)) return;
            event.preventDefault();
            if (event.key === 'Escape') hide();
            else inspect(selected + (event.key === 'ArrowLeft' ? -1 : 1));
        });
        // An exact, screen-reader accessible alternative to hovering a chart.
        const details = document.createElement('details'); details.className = 'analytics-data-note';
        const caption = document.createElement('summary'); caption.textContent = 'View chart data'; details.append(caption);
        const table = document.createElement('table');
        const heading = document.createElement('tr');
        ['Date',...series.map(s=>s.label)].forEach(label => { const cell=document.createElement('th'); cell.scope='col'; cell.textContent=label; heading.append(cell); });
        const thead=document.createElement('thead'); thead.append(heading); table.append(thead);
        const tbody = document.createElement('tbody');
        daily.forEach(day => {const row=document.createElement('tr'); [day.iso,...series.map(s=>format(day[s.key]))].forEach(value=>{const cell=document.createElement('td'); cell.textContent=value; row.append(cell);}); tbody.append(row);});
        table.append(tbody); details.append(table); host.after(details);
    });
})();
