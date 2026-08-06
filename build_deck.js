const pptxgen = require("pptxgenjs");
const p = new pptxgen();
p.layout = "LAYOUT_WIDE"; // 13.33 x 7.5

const NAVY = "0D2B45", NAVY2 = "13385C", TEAL = "1C7293", SEA = "2A9D8F",
      WATER = "2171B5", DANGER = "D7263D", AMBER = "F4A203", INK = "1A2733",
      MUT = "5B6B79", LIGHT = "F4F7FA", WHITE = "FFFFFF";
const W = 13.33;
const SERIF = "Cambria", SANS = "Calibri";

function circleIcon(slide, x, y, d, fill, glyph, gcolor) {
  slide.addShape(p.ShapeType.ellipse, { x, y, w: d, h: d, fill: { color: fill }, line: { color: fill } });
  slide.addText(glyph, { x, y, w: d, h: d, align: "center", valign: "middle",
    fontFace: SANS, fontSize: d * 22, bold: true, color: gcolor || WHITE });
}

/* SLIDE 1 */
let s = p.addSlide();
s.background = { color: NAVY };
s.addShape(p.ShapeType.rect, { x: 0, y: 5.35, w: W, h: 2.15, fill: { color: NAVY2 } });
s.addText("GeoReach", { x: 0.7, y: 0.7, w: 9, h: 0.9, fontFace: SERIF, fontSize: 54, bold: true, color: WHITE });
s.addText("Geospatial Accessibility Intelligence for Flood Response", { x: 0.72, y: 1.62, w: 11.5, h: 0.6, fontFace: SANS, fontSize: 22, color: "CADCFC" });
s.addText([
  { text: "Which villages get cut off when Assam floods — ", options: { color: "CADCFC" } },
  { text: "and who to reach first.", options: { color: AMBER, bold: true } },
], { x: 0.72, y: 2.25, w: 11.8, h: 0.5, fontFace: SANS, fontSize: 18, italic: true });
s.addText("WHY IT MATTERS", { x: 0.72, y: 3.15, w: 6, h: 0.4, fontFace: SANS, fontSize: 14, bold: true, color: SEA, charSpacing: 2 });
s.addText("Every monsoon, the Brahmaputra severs road links between remote settlements and the hospitals that serve them. Relief teams lack a fast, evidence-based map of which places are isolated — and where to send boats first.",
  { x: 0.72, y: 3.55, w: 7.4, h: 1.5, fontFace: SANS, fontSize: 16, color: "DCE6F2", lineSpacingMultiple: 1.15 });
s.addShape(p.ShapeType.roundRect, { x: 8.5, y: 3.05, w: 4.1, h: 2.0, rectRadius: 0.1, fill: { color: NAVY2 }, line: { color: TEAL, width: 1 } });
s.addText("FOCUS AREA", { x: 8.75, y: 3.2, w: 3.6, h: 0.35, fontFace: SANS, fontSize: 12, bold: true, color: SEA, charSpacing: 2 });
s.addText([
  { text: "Dhemaji", options: { bold: true, breakLine: true } },
  { text: "Lakhimpur", options: { bold: true, breakLine: true } },
  { text: "Majuli  ", options: { bold: true } },
  { text: "— world's largest river island", options: { color: "9FB6CC", italic: true } },
], { x: 8.75, y: 3.6, w: 3.6, h: 1.3, fontFace: SANS, fontSize: 17, color: WHITE, lineSpacingMultiple: 1.2 });
[["75+", "lives lost"], ["700K", "people displaced"], ["900+", "villages submerged"]].forEach((st, i) => {
  const x = 0.72 + i * 4.15;
  s.addText(st[0], { x, y: 5.55, w: 3.9, h: 0.85, fontFace: SERIF, fontSize: 46, bold: true, color: AMBER });
  s.addText(st[1], { x, y: 6.45, w: 3.9, h: 0.4, fontFace: SANS, fontSize: 15, color: "CADCFC" });
});
s.addText("Assam floods, 2026", { x: 9.4, y: 6.95, w: 3.2, h: 0.35, align: "right", fontFace: SANS, fontSize: 12, italic: true, color: "7E93A8" });

/* SLIDE 2 */
s = p.addSlide();
s.background = { color: WHITE };
s.addText("Engineering Workflow", { x: 0.7, y: 0.5, w: 10, h: 0.7, fontFace: SERIF, fontSize: 38, bold: true, color: INK });
s.addText("A reproducible pipeline from raw open data to a decision-ready map — end to end in ~23 seconds.",
  { x: 0.72, y: 1.25, w: 12, h: 0.5, fontFace: SANS, fontSize: 16, color: MUT });
const steps = [
  ["1", "Acquire", "OpenStreetMap roads, hospitals, bridges & district boundaries; Census 2011 populations", TEAL],
  ["2", "Map the flood", "Sentinel-1 SAR change detection in Earth Engine; permanent water removed to isolate NEW flooding", WATER],
  ["3", "Build network", "9,090 road segments to a junction-aware graph (connectivity fixed: 27,000 to 144 fragments)", SEA],
  ["4", "Assess access", "Multi-source Dijkstra from every hospital to each village; DEM-derived flood depth", AMBER],
  ["5", "Prioritise", "Weighted score: access loss, population, infrastructure, isolation, depth to ranked triage", DANGER],
];
const cardW = 2.35, gap = 0.18, startX = 0.7, topY = 2.05, cardH = 3.15;
steps.forEach((st, i) => {
  const x = startX + i * (cardW + gap);
  s.addShape(p.ShapeType.roundRect, { x, y: topY, w: cardW, h: cardH, rectRadius: 0.08, fill: { color: LIGHT }, line: { color: "DDE6EE", width: 1 } });
  circleIcon(s, x + cardW / 2 - 0.32, topY + 0.28, 0.64, st[3], st[0]);
  s.addText(st[1], { x: x + 0.1, y: topY + 1.05, w: cardW - 0.2, h: 0.4, align: "center", fontFace: SANS, fontSize: 17, bold: true, color: INK });
  s.addText(st[2], { x: x + 0.16, y: topY + 1.5, w: cardW - 0.32, h: 1.5, align: "center", fontFace: SANS, fontSize: 11.5, color: MUT, lineSpacingMultiple: 1.08 });
  if (i < steps.length - 1)
    s.addText(">", { x: x + cardW - 0.02, y: topY + 1.2, w: gap + 0.04, h: 0.5, align: "center", fontFace: SANS, fontSize: 18, bold: true, color: "9AA9B6" });
});
s.addText("BUILT ENTIRELY ON OPEN / PUBLIC DATA", { x: 0.72, y: 5.55, w: 8, h: 0.35, fontFace: SANS, fontSize: 12, bold: true, color: SEA, charSpacing: 2 });
["Sentinel-1 SAR", "SRTM DEM", "OpenStreetMap", "Census 2011", "CWC gauges", "ASDMA reports"].forEach((c, i) => {
  const x = 0.72 + i * 2.02;
  s.addShape(p.ShapeType.roundRect, { x, y: 5.95, w: 1.9, h: 0.5, rectRadius: 0.25, fill: { color: NAVY }, line: { color: NAVY } });
  s.addText(c, { x, y: 5.95, w: 1.9, h: 0.5, align: "center", valign: "middle", fontFace: SANS, fontSize: 12, bold: true, color: WHITE });
});
s.addText("Python · GeoPandas · NetworkX · rasterio · Folium/Leaflet", { x: 0.72, y: 6.7, w: 12, h: 0.4, fontFace: SANS, fontSize: 13, italic: true, color: MUT });

/* SLIDE 3 */
s = p.addSlide();
s.background = { color: WHITE };
s.addText("Results — Observed 2026 Flood", { x: 0.7, y: 0.5, w: 11, h: 0.7, fontFace: SERIF, fontSize: 38, bold: true, color: INK });
s.addText("Driven by real Sentinel-1 satellite flood extent over the three districts.",
  { x: 0.72, y: 1.25, w: 12, h: 0.4, fontFace: SANS, fontSize: 16, color: MUT });
[["22%", "of roads cut off", DANGER, "2,013 of 9,090 segments"],
 ["49", "settlements isolated", AMBER, "6 critically isolated"],
 ["807 km²", "area flooded", WATER, "depth 0.2 - 12 m (SRTM)"]].forEach((st, i) => {
  const y = 1.85 + i * 1.15;
  s.addText(st[0], { x: 0.7, y, w: 3.2, h: 0.85, fontFace: SERIF, fontSize: 40, bold: true, color: st[2] });
  s.addText(st[1], { x: 3.95, y: y + 0.05, w: 3.2, h: 0.45, fontFace: SANS, fontSize: 17, bold: true, color: INK });
  s.addText(st[3], { x: 3.97, y: y + 0.5, w: 3.3, h: 0.4, fontFace: SANS, fontSize: 12.5, color: MUT });
});
s.addChart(p.ChartType.doughnut, [{ name: "Accessibility", labels: ["Accessible", "Isolated", "Critically isolated", "Partial"], values: [29, 43, 6, 2] }], {
  x: 7.15, y: 1.75, w: 3.0, h: 3.0, holeSize: 58,
  chartColors: [SEA, AMBER, DANGER, "F0C808"], showLegend: false, showTitle: false, showValue: false,
  dataBorder: { pct: 2, color: "FFFFFF" },
});
s.addText("80", { x: 7.15, y: 3.0, w: 3.0, h: 0.5, align: "center", fontFace: SERIF, fontSize: 30, bold: true, color: INK });
s.addText("villages", { x: 7.15, y: 3.5, w: 3.0, h: 0.3, align: "center", fontFace: SANS, fontSize: 12, color: MUT });
[["Accessible 29", SEA], ["Isolated 43", AMBER], ["Critically isolated 6", DANGER], ["Partial 2", "F0C808"]].forEach((l, i) => {
  const y = 1.9 + i * 0.42;
  s.addShape(p.ShapeType.ellipse, { x: 10.35, y: y + 0.03, w: 0.16, h: 0.16, fill: { color: l[1] }, line: { color: l[1] } });
  s.addText(l[0], { x: 10.6, y, w: 2.4, h: 0.3, fontFace: SANS, fontSize: 13, color: INK });
});
s.addShape(p.ShapeType.roundRect, { x: 0.7, y: 5.5, w: 11.93, h: 1.55, rectRadius: 0.08, fill: { color: NAVY } });
s.addText("TOP PRIORITY TO REACH FIRST", { x: 0.95, y: 5.62, w: 6, h: 0.35, fontFace: SANS, fontSize: 12, bold: true, color: SEA, charSpacing: 2 });
[["Jonai", "15,420"], ["Gogamukh", "12,750"], ["Dhakuakhana", "12,800"], ["Kamalabari", "12,200"], ["Bihpuria", "10,500"]].forEach((v, i) => {
  const x = 0.95 + i * 2.34;
  s.addText(String(i + 1), { x, y: 6.05, w: 0.4, h: 0.5, fontFace: SERIF, fontSize: 22, bold: true, color: AMBER });
  s.addText(v[0], { x: x + 0.42, y: 6.05, w: 1.9, h: 0.35, fontFace: SANS, fontSize: 15, bold: true, color: WHITE });
  s.addText("pop " + v[1] + " · critically isolated", { x: x + 0.42, y: 6.42, w: 1.95, h: 0.3, fontFace: SANS, fontSize: 10.5, color: "9FB6CC" });
});

/* SLIDE 4 */
s = p.addSlide();
s.background = { color: NAVY };
s.addText("Innovation & Impact", { x: 0.7, y: 0.55, w: 11, h: 0.7, fontFace: SERIF, fontSize: 38, bold: true, color: WHITE });
const innov = [
  ["S", "Real satellite flood", "Sentinel-1 SAR sees through monsoon cloud; permanent-water exclusion isolates genuinely new inundation", WATER],
  ["E", "Terrain-derived depth", "Flood depth computed from SRTM elevation inside each flooded area — not a flat assumption", SEA],
  ["N", "Junction-aware routing", "Rebuilt the road graph so intersections connect (27,000 to 144 fragments), making isolation real", TEAL],
  ["T", "Multi-criteria triage", "Transparent weighted scoring turns raw geography into a ranked, defensible action list", AMBER],
];
innov.forEach((v, i) => {
  const col = i % 2, row = Math.floor(i / 2);
  const x = 0.7 + col * 6.15, y = 1.55 + row * 1.85;
  s.addShape(p.ShapeType.roundRect, { x, y, w: 5.9, h: 1.65, rectRadius: 0.08, fill: { color: NAVY2 }, line: { color: "1E4A6E", width: 1 } });
  circleIcon(s, x + 0.28, y + 0.42, 0.8, v[3], v[0]);
  s.addText(v[1], { x: x + 1.3, y: y + 0.22, w: 4.4, h: 0.4, fontFace: SANS, fontSize: 18, bold: true, color: WHITE });
  s.addText(v[2], { x: x + 1.3, y: y + 0.66, w: 4.45, h: 0.9, fontFace: SANS, fontSize: 12.5, color: "C4D3E2", lineSpacingMultiple: 1.05 });
});
s.addShape(p.ShapeType.roundRect, { x: 0.7, y: 5.5, w: 11.93, h: 1.5, rectRadius: 0.08, fill: { color: SEA } });
s.addText("IMPACT", { x: 0.95, y: 5.62, w: 4, h: 0.35, fontFace: SANS, fontSize: 12, bold: true, color: "06382F", charSpacing: 2 });
s.addText([
  { text: "Turns days of manual assessment into a 23-second, repeatable run. ", options: { bold: true } },
  { text: "Gives disaster authorities an interactive map of who is cut off and where to send boats first — transferable to any flood-prone district, built entirely on free, public data.", options: {} },
], { x: 0.95, y: 5.98, w: 11.4, h: 0.95, fontFace: SANS, fontSize: 15, color: "042A24", lineSpacingMultiple: 1.1 });

p.writeFile({ fileName: "/sessions/ecstatic-vibrant-meitner/mnt/GeoReach/GeoReach_Presentation.pptx" }).then(f => console.log("WROTE", f));
