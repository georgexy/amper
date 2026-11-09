// Tema „Casă” (cerută 2026-11-07): scena 3D cu casa, panourile, invertorul, bateria și rețeaua, cu fluxurile de
// energie animate. Se împachetează cu three.js într-un singur fișier, web/casa3d.js (tools/casa3d/build.sh), încărcat
// de app.js doar când e aleasă tema. Etichetele și fișa cu detalii sunt HTML în app.js (traduse ca restul aplicației);
// aici: scena, `set()` (noapte/zi și sensul fluxurilor), `anchor()` (unde e pe ecran fiecare element) și atingerea
// (`onPick(kind)`, kind = pv | bat | load | inv | grid).
import * as THREE from "three";
import { RoundedBoxGeometry } from "three/addons/geometries/RoundedBoxGeometry.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";
import { OutputPass } from "three/addons/postprocessing/OutputPass.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

const COL = { pv: 0xffb627, bat: 0x3ddc84, load: 0xa98bff, grid: 0x45b4ff };

export function supported() {
  try {
    if (!window.WebGLRenderingContext) return false;
    const c = document.createElement("canvas");
    const gl = c.getContext("webgl2") || c.getContext("webgl");
    gl?.getExtension("WEBGL_lose_context")?.loseContext();   // eliberat imediat (iPhone: puține contexte WebGL)
    return !!gl;
  } catch { return false; }
}

// modelele de casă (numele și descrierile sunt în app.js, ca să fie traduse)
export const MODEL_IDS = ["clasic", "hambar", "pavilion", "cuburi", "curte", "cabana"];

export function mount(host, { model = "clasic", onPick = () => {}, onFrame = () => {}, onLost = () => {}, onMove = () => {} } = {}) {
  let W = host.clientWidth || 390, H = host.clientHeight || 700;
  const renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "high-performance" });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setSize(W, H);
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  host.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  const sky = document.createElement("canvas"); sky.width = 4; sky.height = 256;
  const skyTex = new THREE.CanvasTexture(sky); skyTex.colorSpace = THREE.SRGBColorSpace;
  scene.background = skyTex;
  scene.fog = new THREE.Fog(0xbcd6e4, 60, 120);
  const pmrem = new THREE.PMREMGenerator(renderer);
  scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;

  const camera = new THREE.PerspectiveCamera(37, W / H, 0.1, 300);
  const LOOK = new THREE.Vector3(0.6, 1.6, 1.2);
  // rotire / apropiere / mutare cu degetele, ca o piesă într-un program CAD (cerut 2026-11-09):
  // un deget = rotește, două degete = apropie/depărtează și mută; mouse: stânga rotește, rotița apropie, dreapta mută
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true; controls.dampingFactor = 0.08;
  controls.minDistance = 10; controls.maxDistance = 120;
  controls.maxPolarAngle = Math.PI * 0.47;                        // nu coboară sub pământ
  controls.screenSpacePanning = true;
  controls.touches = { ONE: THREE.TOUCH.ROTATE, TWO: THREE.TOUCH.DOLLY_PAN };
  let moved = false;
  controls.addEventListener("start", () => { if (!moved) { moved = true; onMove(true); } });
  controls.addEventListener("change", () => {                    // centrul rotirii rămâne pe insulă
    const t = controls.target;
    t.set(Math.max(-24, Math.min(24, t.x)), Math.max(0, Math.min(8, t.y)), Math.max(-24, Math.min(24, t.z)));
  });
  const placeCamera = () => {
    // pe ecrane late, camera mai aproape; pe telefon (portret) mai departe, ca să încapă toată insula
    const f = Math.min(1.25, Math.max(0.78, 0.46 / (W / H)));
    camera.position.set(36 * f, 36 * f, 41 * f);
    controls.target.copy(LOOK);
    camera.lookAt(LOOK);
    controls.update();
  };
  placeCamera();

  const hemi = new THREE.HemisphereLight(0xdfeefe, 0x4d6b3a, 0.8);
  scene.add(hemi);
  const sun = new THREE.DirectionalLight(0xfff1d6, 2.3);
  sun.position.set(-14, 22, 12);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  Object.assign(sun.shadow.camera, { left: -18, right: 18, top: 18, bottom: -18, near: 1, far: 70 });
  sun.shadow.bias = -0.0004; sun.shadow.normalBias = 0.02; sun.shadow.radius = 4;
  scene.add(sun);

  const M = (o) => new THREE.MeshStandardMaterial(o);
  const mat = {
    plaster: M({ color: 0xf3f1ec, roughness: 0.92 }), plaster2: M({ color: 0xe2e3e0, roughness: 0.92 }),
    roof: M({ color: 0x3a4148, roughness: 0.8 }), trim: M({ color: 0x2b3137, roughness: 0.6, metalness: 0.2 }),
    wood: M({ color: 0x9b6a43, roughness: 0.7 }),
    glass: new THREE.MeshPhysicalMaterial({ color: 0x1d3b52, roughness: 0.05, emissive: 0xffa64a, emissiveIntensity: 0,
      envMapIntensity: 1.6, clearcoat: 1 }),
    grass: M({ color: 0x86b86a, roughness: 1 }), soil: M({ color: 0xb9a58a, roughness: 1 }),
    stone: M({ color: 0xd9d6cf, roughness: 0.9 }), alu: M({ color: 0xc8ced3, roughness: 0.35, metalness: 0.8 }),
    white: M({ color: 0xf6f7f8, roughness: 0.35, metalness: 0.05 }), dark: M({ color: 0x1b2128, roughness: 0.4, metalness: 0.2 }),
    leaf: M({ color: 0x5fa35a, roughness: 0.9, flatShading: true }), leaf2: M({ color: 0x7cc06c, roughness: 0.9, flatShading: true }),
    trunk: M({ color: 0x7a5236, roughness: 0.9 }), pole: M({ color: 0x7d8790, roughness: 0.6, metalness: 0.3 }),
    garage: M({ color: 0x9aa4ac, roughness: 0.5, metalness: 0.4 }),
    clad: M({ color: 0x34393e, roughness: 0.55, metalness: 0.35 }), concrete: M({ color: 0xc3c6c6, roughness: 0.95 }),
    deck: M({ color: 0xb48c63, roughness: 0.8 }), wood2: M({ color: 0x8a6a4c, roughness: 0.8 }),
    water: new THREE.MeshPhysicalMaterial({ color: 0x3fb0dc, roughness: 0.08, envMapIntensity: 1.4, clearcoat: 1,
      emissive: 0x2a9fd0, emissiveIntensity: 0 }),
    car: M({ color: 0xd8dde1, roughness: 0.3, metalness: 0.6 }),
  };
  const DAY = {}, NIGHT = {};
  const tone = (m, day, night) => { DAY[m.uuid] = [m, day]; NIGHT[m.uuid] = [m, night]; };
  tone(mat.plaster, 0xf3f1ec, 0xb9bec2); tone(mat.plaster2, 0xe2e3e0, 0x9aa1a7); tone(mat.grass, 0x86b86a, 0x23402c);
  tone(mat.soil, 0xb9a58a, 0x2a2723); tone(mat.stone, 0xd9d6cf, 0x6d7175); tone(mat.leaf, 0x5fa35a, 0x2e5a3c);
  tone(mat.leaf2, 0x7cc06c, 0x3a6e4a); tone(mat.garage, 0x9aa4ac, 0x4a525a); tone(mat.glass, 0x1d3b52, 0xffc477);
  tone(mat.clad, 0x34393e, 0x1c2024); tone(mat.concrete, 0xc3c6c6, 0x73797e); tone(mat.deck, 0xb48c63, 0x4f3e2d);
  tone(mat.wood, 0x9b6a43, 0x5a3f2a); tone(mat.wood2, 0x8a6a4c, 0x4c3a2b); tone(mat.water, 0x3fb0dc, 0x1d6c95);
  tone(mat.car, 0xd8dde1, 0x6f777d);

  // panourile: textură cu celule
  const pc = document.createElement("canvas"); pc.width = 256; pc.height = 416;
  { const g = pc.getContext("2d"); const gr = g.createLinearGradient(0, 0, 256, 416);
    gr.addColorStop(0, "#1f4f86"); gr.addColorStop(1, "#0d2445"); g.fillStyle = gr; g.fillRect(0, 0, 256, 416);
    g.strokeStyle = "rgba(170,205,240,.45)"; g.lineWidth = 2;
    for (let i = 1; i < 6; i++) { g.beginPath(); g.moveTo(i * 256 / 6, 0); g.lineTo(i * 256 / 6, 416); g.stroke(); }
    for (let j = 1; j < 10; j++) { g.beginPath(); g.moveTo(0, j * 416 / 10); g.lineTo(256, j * 416 / 10); g.stroke(); } }
  const panTex = new THREE.CanvasTexture(pc); panTex.colorSpace = THREE.SRGBColorSpace; panTex.anisotropy = 8;
  const panelMat = new THREE.MeshPhysicalMaterial({ map: panTex, roughness: 0.6, metalness: 0.05, clearcoat: 0.08,
    clearcoatRoughness: 0.7, specularIntensity: 0.35, envMapIntensity: 0.6 });

  let KIND = null;
  const add = (geo, m, x, y, z, o = {}) => {
    const mesh = new THREE.Mesh(geo, m); mesh.position.set(x, y, z); mesh.userData.kind = KIND;
    mesh.castShadow = o.cast ?? true; mesh.receiveShadow = o.receive ?? true;
    if (o.rot) mesh.rotation.set(...o.rot);
    (o.parent || scene).add(mesh); return mesh;
  };
  const box = (w, h, d, m, x, y, z, o) => add(new THREE.BoxGeometry(w, h, d), m, x, y, z, o);
  const rbox = (w, h, d, r, m, x, y, z, o) => add(new RoundedBoxGeometry(w, h, d, 4, r), m, x, y, z, o);

  // ---- terenul comun: o insulă cu margini rotunjite (x −11…13, z −9…10)
  rbox(24, 1.2, 19, 0.5, mat.soil, 1, -0.62, 0.5, { cast: false });
  rbox(23.6, 0.3, 18.6, 0.4, mat.grass, 1, -0.05, 0.5, { cast: false });

  // ================= piese comune tuturor modelelor de casă =================
  const V = (x, y, z) => new THREE.Vector3(x, y, z);
  // fereastră pe un perete cu fața spre +z („z”) sau spre +x („x”)
  const win = (w, h, x, y, z, face = "z") => {
    if (face === "z") { box(w + 0.14, h + 0.14, 0.1, mat.trim, x, y, z); box(w, h, 0.06, mat.glass, x, y, z + 0.04); }
    else { box(0.1, h + 0.14, w + 0.14, mat.trim, x, y, z); box(0.06, h, w, mat.glass, x + 0.04, y, z); }
  };
  // profil (z, y) extrudat de-a lungul axei x, centrat în x: pereți cu fronton, corpuri cu acoperiș înclinat
  const profileX = (pts, len, m, x, o) => {
    const s = new THREE.Shape(pts.map(([z, y]) => new THREE.Vector2(-z, y)));
    const g = new THREE.ExtrudeGeometry(s, { depth: len, bevelEnabled: false });
    g.translate(0, 0, -len / 2);
    return add(g, m, x, 0, 0, { ...o, rot: [0, Math.PI / 2, 0] });
  };
  // casă cu acoperiș în două ape, coama pe axa x. Întoarce unghiul, lungimea pantei și `pt(side, x, s, off)` =
  // punctul de pe acoperiș la distanța s de coamă (side +1 = panta dinspre +z), la `off` deasupra tablei.
  const gable = ({ x = 0, z = 0, len, wid, h, r, body, roofM = mat.roof, over = 0.12, endOver = 0.1, t = 0.18 }) => {
    profileX([[z - wid / 2, 0.1], [z + wid / 2, 0.1], [z + wid / 2, h], [z, r], [z - wid / 2, h]], len, body, x);
    const a = Math.atan2(r - h, wid / 2), L = Math.hypot(wid / 2, r - h) + over, c = Math.cos(a), s = Math.sin(a);
    for (const sd of [1, -1])
      box(len + 2 * endOver, t, L, roofM, x, r - s * L / 2 + c * t / 2, z + sd * (c * L / 2 + s * t / 2), { rot: [sd * a, 0, 0] });
    box(len + 2 * endOver, 0.1, 0.34, roofM, x, r + t * c + 0.02, z);                    // coama
    const pt = (sd, px, d, off = 0.1) => [px, r - d * s + (t + off) * c, z + sd * (d * c + (t + off) * s)];
    return { a, L, pt };
  };
  // panouri culcate pe o pantă: grup așezat pe acoperiș, cu panourile în planul lui
  const slopePanels = (pos, rotX, rows, cols, dx = 2.15, dz = 1.82) => {
    const g = new THREE.Group(); g.position.set(...pos); g.rotation.set(rotX, 0, 0); scene.add(g);
    KIND = "pv";
    for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) {
      const px = (c - (cols - 1) / 2) * dx, pz = (r - (rows - 1) / 2) * dz;
      add(new THREE.BoxGeometry(2.06, 0.05, 1.76), mat.dark, px, 0.0, pz, { parent: g });     // ramă neagră
      add(new THREE.BoxGeometry(2.0, 0.05, 1.7), panelMat, px, 0.025, pz, { parent: g });
    }
    return g;
  };
  // panouri pe acoperiș plat: înclinate, pe suporturi de aluminiu (xs, zs = centrele coloanelor și rândurilor)
  const flatPanels = (xs, zs, yRoof) => {
    KIND = "pv";
    for (const z of zs) for (const x of xs) {
      const y = yRoof + 0.44;
      add(new THREE.BoxGeometry(2.0, 0.05, 1.7), panelMat, x, y + 0.42, z, { rot: [0.28, 0, 0] });
      add(new THREE.BoxGeometry(2.06, 0.06, 1.76), mat.alu, x, y + 0.4, z, { rot: [0.28, 0, 0] });
      box(0.06, 0.55, 0.06, mat.alu, x - 0.9, y + 0.15, z - 0.6); box(0.06, 0.55, 0.06, mat.alu, x + 0.9, y + 0.15, z - 0.6);
    }
  };
  // invertorul, pe un perete: face „x” = peretele e planul x = wall (c = z), face „z” = planul z = wall (c = x)
  const ledMat = new THREE.MeshBasicMaterial({ color: 0x3ddc84 });
  const inverter = (wall, y, c, face = "x") => {
    KIND = "inv";
    if (face === "x") {
      rbox(0.22, 1.05, 0.8, 0.06, mat.white, wall + 0.06, y, c);
      box(0.03, 0.22, 0.5, mat.dark, wall + 0.17, y + 0.22, c, { cast: false });
      box(0.03, 0.04, 0.28, ledMat, wall + 0.175, y - 0.2, c, { cast: false });
    } else {
      rbox(0.8, 1.05, 0.22, 0.06, mat.white, c, y, wall + 0.06);
      box(0.5, 0.22, 0.03, mat.dark, c, y + 0.22, wall + 0.17, { cast: false });
      box(0.28, 0.04, 0.03, ledMat, c, y - 0.2, wall + 0.175, { cast: false });
    }
  };
  // bateria: turn de 4 module lipit de perete; benzile verzi arată cât e încărcată
  const batLeds = [];
  const battery = (wall, c, face = "x", y0 = 0) => {
    KIND = "bat";
    for (let i = 0; i < 4; i++) {
      const y = y0 + 0.32 + i * 0.45, m = new THREE.MeshBasicMaterial({ color: 0x3ddc84 });
      batLeds.push(m);
      if (face === "x") { rbox(0.55, 0.42, 0.95, 0.05, mat.white, wall + 0.35, y, c); box(0.02, 0.05, 0.6, m, wall + 0.63, y, c, { cast: false }); }
      else { rbox(0.95, 0.42, 0.55, 0.05, mat.white, c, y, wall + 0.35); box(0.6, 0.05, 0.02, m, c, y, wall + 0.63, { cast: false }); }
    }
    if (face === "x") rbox(0.6, 0.12, 1.0, 0.04, mat.dark, wall + 0.35, y0 + 0.14, c);
    else rbox(1.0, 0.12, 0.6, 0.04, mat.dark, c, y0 + 0.14, wall + 0.35);
  };
  // stâlpul rețelei și cablul aerian până la casă (to = punctul de prindere pe casă)
  const POLE = V(-10.0, 0, -1.5);
  const gridLine = (to) => {
    KIND = "grid";
    add(new THREE.CylinderGeometry(0.12, 0.16, 8.4, 12), mat.pole, POLE.x, 4.2, POLE.z);
    box(2.0, 0.12, 0.12, mat.pole, POLE.x, 7.9, POLE.z);
    const a = V(POLE.x, 7.85, POLE.z), b = V(...to);
    const mid = a.clone().lerp(b, 0.5); mid.y -= 0.35;                 // săgeata cablului
    const pts = [a, mid, b];
    add(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pts), 24, 0.025, 6), mat.dark, 0, 0, 0, { cast: false });
    return pts;
  };
  const tree = (x, z, s = 1) => {
    KIND = null;
    add(new THREE.CylinderGeometry(0.12 * s, 0.16 * s, 1.8 * s, 8), mat.trunk, x, 0.9 * s, z);
    add(new THREE.IcosahedronGeometry(1.1 * s, 1), mat.leaf, x, 2.2 * s, z);
    add(new THREE.IcosahedronGeometry(0.8 * s, 1), mat.leaf2, x + 0.5 * s, 2.7 * s, z + 0.3 * s);
    add(new THREE.IcosahedronGeometry(0.7 * s, 1), mat.leaf2, x - 0.5 * s, 2.5 * s, z - 0.2 * s);
  };
  const pine = (x, z, s = 1) => {
    KIND = null;
    add(new THREE.CylinderGeometry(0.1 * s, 0.14 * s, 1.0 * s, 8), mat.trunk, x, 0.5 * s, z);
    for (const [r, h, y] of [[1.15, 1.7, 1.6], [0.9, 1.5, 2.6], [0.6, 1.3, 3.5]])
      add(new THREE.ConeGeometry(r * s, h * s, 8), mat.leaf, x, y * s, z);
  };
  const bush = (x, z, s) => { KIND = null; add(new THREE.IcosahedronGeometry(s, 1), mat.leaf2, x, s * 0.7, z); };
  const fence = (x0, n, z) => {
    KIND = null;
    for (let i = 0; i < n; i++) box(0.08, 0.55, 0.08, mat.white, x0 + i * 0.75, 0.38, z, { cast: false });
    box(n * 0.75 - 0.5, 0.06, 0.06, mat.white, x0 + (n - 1) * 0.375, 0.55, z, { cast: false });
  };
  // mașină simplă, cu fața spre +z
  const car = (x, z) => {
    KIND = null;
    rbox(1.8, 0.55, 4.2, 0.22, mat.car, x, 0.62, z); rbox(1.62, 0.5, 2.2, 0.2, mat.dark, x, 1.08, z - 0.2);
    for (const [dx, dz] of [[-0.86, 1.3], [0.86, 1.3], [-0.86, -1.3], [0.86, -1.3]])
      add(new THREE.CylinderGeometry(0.34, 0.34, 0.24, 16), mat.dark, x + dx, 0.38, z + dz, { rot: [0, 0, Math.PI / 2] });
  };
  // noaptea: lumina caldă din casă, felinarele de pe alee, stelele
  const nightObjs = [];
  const glow = (x, y, z, power = 7, dist = 8) => {
    const l = new THREE.PointLight(0xffb55e, power, dist, 1.6); l.position.set(x, y, z); scene.add(l); nightObjs.push(l);
  };
  const lampMat = new THREE.MeshBasicMaterial({ color: new THREE.Color(0xffd28a).multiplyScalar(4) });
  const lamp = (x, z, y0 = 0) => {
    KIND = null;
    box(0.08, 0.7, 0.08, mat.dark, x, y0 + 0.45, z, { cast: false });
    const bulb = add(new THREE.SphereGeometry(0.12, 12, 8), lampMat, x, y0 + 0.85, z, { cast: false });
    const l = new THREE.PointLight(0xffc77a, 3, 3.5, 1.8); l.position.set(x, y0 + 0.9, z); scene.add(l);
    nightObjs.push(l, bulb);
  };
  const starGeo = new THREE.BufferGeometry(), sp = [];
  for (let i = 0; i < 300; i++) sp.push(-30 + (Math.random() - 0.5) * 120, 20 + Math.random() * 50, -40 + (Math.random() - 0.5) * 60);
  starGeo.setAttribute("position", new THREE.Float32BufferAttribute(sp, 3));
  const stars = new THREE.Points(starGeo, new THREE.PointsMaterial({ color: 0xcfe3f2, size: 0.35, fog: false }));
  scene.add(stars); nightObjs.push(stars);

  // cablurile merg pe pereți și pe acoperiș, ca niște trasee reale (segmente drepte, colțuri rotunjite, la ~10 cm de
  // suprafețe), nu prin ziduri; pe ele, particulele luminoase
  const conduit = (pts, r = 0.25) => {
    const P = pts.map((p) => new THREE.Vector3(...p)), path = new THREE.CurvePath();
    let prev = P[0].clone();
    const line = (a, b) => { if (a.distanceTo(b) > 1e-3) path.add(new THREE.LineCurve3(a, b)); };
    for (let i = 1; i < P.length; i++) {
      const cur = P[i];
      if (i === P.length - 1) { line(prev, cur.clone()); break; }
      const a = cur.clone().sub(P[i - 1]), b = P[i + 1].clone().sub(cur);
      const ra = Math.min(r, a.length() / 2), rb = Math.min(r, b.length() / 2);
      const p1 = cur.clone().sub(a.normalize().multiplyScalar(ra)), p2 = cur.clone().add(b.normalize().multiplyScalar(rb));
      line(prev, p1);
      path.add(new THREE.QuadraticBezierCurve3(p1, cur.clone(), p2));
      prev = p2;
    }
    return path;
  };

  // ================= modelele de casă =================
  // Fiecare își construiește clădirea și întoarce: traseele cablurilor (pv = panouri → invertor, bat = invertor →
  // baterie, load = invertor → casă), punctul de prindere al cablului de rețea (wire), ancorele etichetelor (anch)
  // și, opțional, punctul spre care se uită camera (look). Invertorul: 0,8 lat × 1,05 înalt; cablurile de pe perete
  // stau la 12 cm de el și intră în invertor pe sus (panouri, casă) sau pe jos (baterie).
  const MODELS = {
    // ---- vilă cu etaj în consolă, acoperiș plat și garaj (primul model)
    clasic() {
      KIND = null;
      box(2.2, 0.06, 6.5, mat.stone, 2.0, 0.12, 6.4, { cast: false });
      box(10.2, 0.1, 2.0, mat.stone, -0.3, 0.12, 4.3, { cast: false });
      KIND = "load";
      const HX = -0.5, HZ = 0;
      box(9, 3.1, 7, mat.plaster, HX, 1.65, HZ);
      box(9.8, 3.0, 7.6, mat.plaster, HX + 0.3, 4.7, HZ + 0.2);
      box(10.2, 0.32, 8.0, mat.roof, HX + 0.3, 6.25, HZ + 0.2);
      for (const [w, d, x, z] of [[10.2, 0.2, 0, -3.8], [10.2, 0.2, 0, 4.2], [0.2, 8, -4.8, 0.2], [0.2, 8, 5.4, 0.2]])
        box(w, 0.5, d, mat.plaster2, HX + 0.3 + x, 6.6, z);
      for (let i = 0; i < 16; i++) box(0.12, 2.9, 0.08, mat.wood, HX + 1.2 + i * 0.22, 4.6, HZ + 4.04, { cast: false });
      win(3.2, 2.2, HX - 2.2, 4.7, HZ + 4.02); win(5.0, 2.3, HX + 0.6, 1.55, HZ + 3.53); win(1.6, 2.0, HX - 3.4, 1.55, HZ + 3.53);
      win(2.4, 1.6, HX + 5.22, 4.7, HZ - 1.2, "x"); win(2.4, 1.6, HX + 5.22, 4.7, HZ + 2.0, "x"); win(3.0, 2.0, HX + 4.52, 1.6, HZ + 0.4, "x");
      box(1.2, 2.5, 0.1, mat.wood, HX + 3.5, 1.35, HZ + 3.53);
      const GX = 6.6, GZ = 0.6;                    // garajul: pereți x 4,4…8,8, z −2,4…3,6, acoperiș sus la y 3,345
      box(4.4, 3.0, 6.0, mat.plaster2, GX, 1.6, GZ);
      box(4.7, 0.25, 6.3, mat.roof, GX, 3.22, GZ);
      box(3.2, 2.3, 0.08, mat.garage, GX - 0.1, 1.27, GZ + 3.02);
      for (let i = 0; i < 6; i++) box(3.2, 0.03, 0.04, mat.trim, GX - 0.1, 0.35 + i * 0.38, GZ + 3.07, { cast: false });
      flatPanels([-3.5, -1.35, 0.8, 2.95], [-2.4, -0.05, 2.3], 6.41);
      const IZ = -0.3, WX = 8.92, PZ = 0.35;       // PZ: între cele două ferestre laterale ale etajului
      inverter(8.8, 1.75, IZ, "x");
      battery(8.8, 1.7, "x");
      const wire = gridLine([HX - 4.95, 5.6, HZ - 0.5]);
      tree(-8.2, 6.6, 1.15); tree(10.8, -5.6, 1.0); tree(-8.6, -6.2, 0.95); tree(11.0, 7.4, 0.75);
      bush(-3.6, 5.2, 0.5); bush(-2.6, 5.4, 0.42); bush(5.2, 5.6, 0.45); bush(-5.4, 3.8, 0.4);
      fence(-10.5, 18, 9.4);
      glow(HX + 0.6, 1.6, HZ + 5.2); glow(HX - 2.2, 4.7, HZ + 5.4); glow(HX + 6.2, 4.7, HZ + 0.4);
      lamp(3.4, 4.6); lamp(3.4, 6.6); lamp(3.4, 8.6);
      return {
        pv: [[3.6, 6.55, PZ], [4.95, 6.55, PZ], [4.95, 6.98, PZ], [5.45, 6.98, PZ], [5.45, 3.43, PZ], [WX + 0.12, 3.43, PZ],
             [WX + 0.12, 2.75, PZ], [WX, 2.75, PZ], [WX, 2.75, IZ], [WX, 2.28, IZ]],
        bat: [[WX, 1.22, IZ - 0.25], [WX, 0.92, IZ - 0.25], [WX, 0.92, 1.2]],
        load: [[WX, 1.22, IZ + 0.25], [WX, 1.1, IZ + 0.25], [WX, 1.1, IZ + 1.0], [WX, 2.8, IZ + 1.0], [WX, 2.8, GZ + 3.12],
               [GX - 2.2, 2.8, GZ + 3.12], [HX + 4.35, 2.8, GZ + 3.12], [HX + 4.35, 2.8, HZ + 3.56]],
        wire, look: [0.6, 1.6, 1.2],
        anch: { pv: [1.0, 8.6, -1.5], load: [-1.5, 2.4, 5.6], inv: [6.0, 4.1, -0.6], bat: [9.75, 0.0, 5.9] },
      };
    },

    // ---- hambar modern: două ape fără streașină, placare închisă, fronton vitrat, panouri în planul acoperișului
    hambar() {
      KIND = null;
      box(9.3, 0.14, 2.6, mat.deck, 1.25, 0.17, 4.9, { cast: false });                    // terasă de lemn
      box(1.8, 0.06, 3.6, mat.stone, -2.0, 0.12, 8.0, { cast: false });
      KIND = "load";
      const G = gable({ len: 12, wid: 7, h: 3.2, r: 6.4, body: mat.clad });
      box(2.4, 2.6, 0.12, mat.wood, -2.0, 1.4, 3.5);                                       // intrarea, în lemn
      box(1.0, 2.2, 0.06, mat.trim, -2.0, 1.25, 3.58);
      win(3.0, 2.2, 1.6, 1.5, 3.52); win(1.6, 1.4, -4.7, 1.8, 3.52);
      win(5.2, 2.5, 6.02, 1.55, 0, "x");                                                   // frontonul vitrat
      profileX([[-2.75, 3.25], [2.75, 3.25], [0, 5.75]], 0.1, mat.trim, 6.0);
      profileX([[-2.45, 3.35], [2.45, 3.35], [0, 5.55]], 0.06, mat.glass, 6.06);
      box(0.5, 1.3, 0.5, mat.clad, -3.2, 6.0, -1.6);                                       // coșul
      slopePanels(G.pt(1, 0, 2.15, 0.02), G.a, 2, 5);
      inverter(3.5, 1.75, 5.0, "z");
      battery(3.5, 3.95, "z", 0.14);
      const wire = gridLine([-6.05, 4.9, 0]);
      tree(-8.6, 6.4, 1.1); tree(10.6, -5.4, 1.05); tree(-8.4, -5.8, 0.9); tree(11.2, 6.2, 0.8);
      bush(-4.6, 4.6, 0.45); bush(-5.4, 4.3, 0.38); bush(7.4, 4.4, 0.42);
      fence(-10.5, 12, 9.4);
      glow(1.6, 1.6, 5.2); glow(7.6, 2.2, 0, 8, 9); glow(-2.0, 1.6, 5.0, 4);
      lamp(-0.8, 7.0); lamp(-0.8, 9.0);
      const W = 3.62;
      return {
        pv: [G.pt(1, 5.25, 3.95), G.pt(1, 5.25, G.L + 0.06), [5.25, 2.85, W], [5.25, 2.28, W]],
        bat: [[4.75, 1.22, W], [4.75, 0.95, W], [4.45, 0.95, W]],
        load: [[4.8, 2.28, W], [4.8, 2.9, W], [-2.0, 2.9, W], [-2.0, 2.75, 3.52]],
        wire, look: [0.4, 1.9, 0.8],
        anch: { pv: [0, 6.9, 1.0], load: [-2.6, 2.3, 5.4], inv: [5.2, 3.3, 3.8], bat: [3.4, -0.4, 9.0] },
      };
    },

    // ---- pavilion pe un nivel, acoperiș într-o apă plin de panouri, fațadă vitrată sub streașină, terasă
    pavilion() {
      KIND = null;
      box(13.6, 0.14, 3.0, mat.deck, 0, 0.17, 5.1, { cast: false });
      box(1.8, 0.06, 3.2, mat.stone, 1.8, 0.12, 8.2, { cast: false });
      KIND = "load";
      const TAN = 2 / 7, A = Math.atan(TAN), C = Math.cos(A);
      profileX([[-3.5, 0.1], [3.5, 0.1], [3.5, 3.0], [-3.5, 5.0]], 13, mat.plaster, 0);
      const yLine = (z) => 5 - (z + 3.5) * TAN;                                           // muchia de sus a pereților
      const roofPt = (x, z, off = 0.1) => [x, yLine(z) + (0.22 + off) / C, z];
      box(13.6, 0.22, 8.1 / C, mat.roof, 0, yLine(0.35) + 0.11 / C, 0.35, { rot: [A, 0, 0] });   // streașină 0,9 m în față
      win(6.4, 2.3, -2.4, 1.45, 3.52); win(2.6, 2.3, 4.4, 1.45, 3.52);
      box(1.5, 2.7, 0.08, mat.wood, 1.8, 1.45, 3.5); box(1.0, 2.2, 0.06, mat.trim, 1.8, 1.25, 3.56);
      win(1.2, 2.6, 6.52, 1.6, -1.8, "x");
      KIND = null;
      rbox(2.2, 0.08, 1.0, 0.03, mat.wood2, -3.4, 0.98, 5.4);                              // masa de pe terasă
      for (const dx of [-0.9, 0.9]) box(0.08, 0.7, 0.8, mat.dark, -3.4 + dx, 0.6, 5.4);
      for (const dz of [-0.85, 0.85]) { rbox(2.0, 0.07, 0.4, 0.03, mat.wood2, -3.4, 0.68, 5.4 + dz); box(1.8, 0.4, 0.06, mat.dark, -3.4, 0.45, 5.4 + dz); }
      box(0.5, 1.4, 0.5, mat.roof, -4.6, 5.3, -2.2);                                       // coșul
      KIND = "load";
      for (let i = 0; i < 6; i++) box(0.1, 2.7, 0.08, mat.wood, -6.3 + i * 0.16, 1.5, 3.52, { cast: false });
      slopePanels(roofPt(0, -0.25, 0.02), A, 3, 5, 2.15, 1.9);
      inverter(6.5, 1.75, 1.5, "x");
      battery(6.5, 0.3, "x");
      const wire = gridLine([-6.55, 4.0, -1.6]);
      tree(-8.8, 6.8, 1.1); tree(10.9, -5.8, 1.0); tree(-8.6, -6.0, 0.95); tree(11.4, 5.4, 0.7);
      bush(8.0, 3.2, 0.45); bush(8.8, 2.6, 0.36); bush(-7.6, 4.2, 0.42);
      fence(-10.5, 14, 9.4);
      glow(-2.4, 1.5, 5.2, 8); glow(4.4, 1.5, 5.0); glow(7.8, 1.8, -1.8, 5);
      lamp(3.2, 7.4); lamp(3.2, 9.2); lamp(-6.4, 6.2, 0.14);
      const WX = 6.62, W = 3.62;
      return {
        pv: [roofPt(5.5, 1.3), roofPt(6.92, 1.3), [6.92, 3.3, 1.3], [WX, 3.3, 1.3], [WX, 2.28, 1.3]],
        bat: [[WX, 1.22, 1.25], [WX, 0.92, 1.25], [WX, 0.92, 0.8]],
        load: [[WX, 2.28, 1.75], [WX, 2.85, 1.75], [WX, 2.85, W], [1.8, 2.85, W], [1.8, 2.82, 3.52]],
        wire, look: [0.4, 1.4, 1.0],
        anch: { pv: [0, 5.6, -0.6], load: [-2.4, 2.3, 5.6], inv: [6.9, 3.9, 1.6], bat: [7.6, 0.0, 5.0] },
      };
    },

    // ---- două cuburi decalate: parter din beton, etaj închis la culoare în consolă peste carport, piscină
    cuburi() {
      KIND = null;
      box(4.6, 0.06, 5.2, mat.stone, 4.8, 0.12, 0.8, { cast: false });                    // carportul de sub consolă
      box(1.6, 0.06, 6.6, mat.stone, 0.3, 0.12, 6.4, { cast: false });
      box(5.6, 0.16, 3.2, mat.stone, 7.2, 0.14, 7.0, { cast: false });                    // piscina
      box(5.0, 0.06, 2.6, mat.water, 7.2, 0.22, 7.0, { cast: false });
      box(5.6, 0.12, 1.3, mat.deck, 7.2, 0.14, 4.75, { cast: false });
      for (const x of [6.3, 8.1]) {                                                        // șezlonguri
        rbox(0.7, 0.1, 1.05, 0.04, mat.white, x, 0.42, 4.85); box(0.6, 0.2, 0.9, mat.dark, x, 0.3, 4.85, { cast: false });
        rbox(0.7, 0.1, 0.6, 0.04, mat.white, x, 0.58, 4.2, { rot: [-0.7, 0, 0] });
      }
      KIND = "load";
      box(8, 3.1, 6, mat.concrete, -1.5, 1.65, 0);                                        // parter: x −5,5…2,5, z −3…3
      box(11, 2.9, 5, mat.clad, 1.5, 4.65, 0.8);                                          // etaj: x −4…7, z −1,7…3,3
      for (const [w, d, x, z] of [[11, 0.18, 1.5, -1.61], [11, 0.18, 1.5, 3.21], [0.18, 5, -3.91, 0.8], [0.18, 5, 6.91, 0.8]])
        box(w, 0.3, d, mat.clad, x, 6.25, z);
      box(10.6, 0.06, 4.6, mat.roof, 1.5, 6.12, 0.8, { cast: false });
      box(0.14, 3.1, 0.14, mat.dark, 6.7, 1.65, -1.4); box(0.14, 3.1, 0.14, mat.dark, 6.7, 1.65, 3.0);
      win(7.0, 2.0, 2.2, 4.65, 3.32); win(2.4, 2.0, 7.02, 4.65, 0.3, "x");
      for (let i = 0; i < 9; i++) box(0.12, 2.5, 0.08, mat.wood, -3.6 + i * 0.24, 4.65, 3.34, { cast: false });
      win(4.0, 2.2, -2.9, 1.5, 3.02);
      box(1.2, 2.5, 0.1, mat.wood, 0.3, 1.35, 3.03);
      flatPanels([-2.2, -0.05, 2.1, 4.25], [-0.3, 2.0], 6.15);
      inverter(2.5, 1.75, 2.2, "x");
      battery(2.5, 1.0, "x");
      const wire = gridLine([-4.05, 5.6, 0.4]);
      tree(-8.6, 6.6, 1.1); tree(11.0, -5.6, 1.0); tree(-8.4, -6.0, 0.95);
      bush(-3.4, 4.2, 0.45); bush(-4.3, 4.0, 0.38); bush(11.4, 7.0, 0.5); bush(3.4, 8.2, 0.4);
      fence(-10.5, 13, 9.4);
      glow(-2.9, 1.5, 4.6); glow(2.2, 4.65, 5.0, 8); glow(8.4, 4.6, 0.3, 6); glow(4.6, 2.6, 1.0, 4, 6);
      lamp(1.6, 5.0); lamp(1.6, 7.4); lamp(3.9, 8.9);
      const WX = 2.62, R = 2.2;
      return {
        pv: [[4.6, 6.3, R], [6.82, 6.3, R], [6.82, 6.52, R], [7.12, 6.52, R], [7.12, 3.08, R], [WX, 3.08, R], [WX, 2.28, R]],
        bat: [[WX, 1.22, 2.0], [WX, 0.92, 2.0], [WX, 0.92, 1.5]],
        load: [[WX, 2.28, 2.45], [WX, 2.9, 2.45], [WX, 2.9, 3.12], [0.3, 2.9, 3.12], [0.3, 2.75, 3.02]],
        wire, look: [1.4, 1.8, 1.4],
        anch: { pv: [1.0, 8.3, -0.4], load: [-2.9, 2.3, 5.0], inv: [3.0, 2.9, 2.6], bat: [3.4, 0.0, 5.6] },
      };
    },

    // ---- casă pe un nivel în L, cu curte interioară, terasă și carport solar
    curte() {
      KIND = null;
      box(7, 0.14, 4.6, mat.deck, 1.5, 0.17, 2.9, { cast: false });                       // curtea: terasă de lemn
      box(4.8, 0.06, 6.0, mat.stone, 9.3, 0.12, -1.2, { cast: false });                   // sub carport
      box(3.0, 0.06, 8.0, mat.stone, 9.3, 0.12, 5.8, { cast: false });                    // aleea mașinii
      KIND = "load";
      box(12, 3.2, 5, mat.plaster, -1, 1.7, -2);                                          // aripa A: x −7…5, z −4,5…0,5
      box(13.2, 0.3, 6.2, mat.roof, -1, 3.45, -2);
      box(5, 3.2, 6, mat.plaster, -4.5, 1.7, 3.5);                                        // aripa B: x −7…−2, z 0,5…6,5
      box(6.2, 0.3, 6.0, mat.roof, -4.5, 3.45, 4.1);
      win(4.0, 2.4, 0.3, 1.5, 0.52); win(4.0, 2.2, -1.98, 1.5, 3.6, "x");
      for (let i = 0; i < 20; i++) box(0.13, 3.0, 0.08, mat.wood, -6.78 + i * 0.24, 1.7, 6.54, { cast: false });
      win(2.4, 1.3, 5.02, 1.9, -2.6, "x");
      flatPanels([-5.0, -2.85, -0.7, 1.45], [-3.5, -1.2], 3.6);
      // carportul solar: stâlpi subțiri, acoperișul = panouri
      KIND = null;
      for (const [x, h, z] of [[7.1, 3.0, -3.9], [11.5, 3.0, -3.9], [7.1, 2.3, 1.5], [11.5, 2.3, 1.5]]) box(0.12, h, 0.12, mat.dark, x, h / 2 + 0.1, z);
      const CA = Math.atan(0.7 / 5.4);
      box(4.9, 0.08, 5.9, mat.dark, 9.3, 2.76, -1.2, { rot: [CA, 0, 0] });
      slopePanels([9.3, 2.83, -1.2], CA, 3, 2, 2.15, 1.85);
      car(9.3, -1.3);
      inverter(0.5, 1.75, 4.4, "z");
      battery(0.5, 3.2, "z", 0.14);
      const wire = gridLine([-7.05, 3.05, 2.6]);
      tree(-9.0, 8.2, 0.9); tree(11.6, -6.6, 0.95); tree(-9.2, -6.4, 1.0); tree(4.2, 7.6, 0.85);
      bush(-1.2, 6.2, 0.45); bush(5.6, 3.4, 0.4); bush(5.9, 2.5, 0.32);
      fence(-10.5, 10, 9.4);
      glow(0.3, 1.5, 2.2, 8); glow(-0.4, 1.5, 3.8); glow(9.3, 2.0, -1.2, 5, 6); glow(6.2, 1.9, -2.6, 4);
      lamp(-1.6, 5.0, 0.14); lamp(4.6, 5.0, 0.14); lamp(7.4, 7.6);
      const W = 0.62;
      return {
        pv: [[2.6, 3.72, -2.2], [4.65, 3.72, -2.2], [4.65, 3.72, 1.22], [4.65, 3.18, 1.22], [4.65, 3.18, W], [4.65, 2.28, W]],
        bat: [[4.15, 1.22, W], [4.15, 0.95, W], [3.7, 0.95, W]],
        load: [[4.2, 2.28, W], [4.2, 2.95, W], [0.3, 2.95, W], [0.3, 2.85, 0.52]],
        wire, look: [1.0, 1.2, 0.4],
        anch: { pv: [-1.8, 5.6, -2.4], load: [-3.0, 2.2, 7.2], inv: [4.6, 3.9, 0.9], bat: [3.4, 0.0, 6.4] },
      };
    },

    // ---- cabană în A: acoperiș până la pământ, fronton de sticlă spre terasă, anexă tehnică din lemn
    cabana() {
      KIND = null;
      box(4.5, 0.3, 7.4, mat.deck, 7.0, 0.2, 0, { cast: false });
      box(1.6, 0.06, 4.0, mat.stone, 7.0, 0.12, 5.7, { cast: false });
      KIND = "load";
      const G = gable({ len: 9, wid: 8, h: 0.4, r: 7.2, body: mat.wood, over: 0.15, endOver: 0.5 });
      profileX([[-3.45, 0.42], [3.45, 0.42], [0, 6.25]], 0.1, mat.trim, 4.5);             // frontonul de sticlă
      profileX([[-3.2, 0.55], [3.2, 0.55], [0, 5.95]], 0.06, mat.glass, 4.56);
      box(0.1, 5.4, 0.12, mat.trim, 4.6, 3.25, 0, { cast: false }); box(0.1, 0.12, 3.8, mat.trim, 4.6, 2.75, 0, { cast: false });
      box(0.08, 2.1, 1.0, mat.wood, 4.62, 1.5, -1.25);                                    // ușa
      add(new THREE.CylinderGeometry(0.18, 0.18, 1.7, 12), mat.alu, -2.5, 6.6, -1.0);     // coșul sobei
      box(3.0, 2.6, 3.9, mat.wood2, -1.7, 1.3, 4.45);                                     // anexa: x −3,2…−0,2, z 2,5…6,4
      box(3.1, 0.15, 3.95, mat.roof, -1.7, 2.675, 4.575);
      slopePanels(G.pt(1, 0.1, 2.5, 0.02), G.a, 2, 4);
      inverter(-0.2, 1.6, 4.6, "x");
      battery(-0.2, 5.7, "x");
      const wire = gridLine([-4.55, 5.0, 0]);
      pine(-8.4, 6.2, 1.25); pine(-6.6, 7.8, 0.95); pine(10.8, -5.8, 1.2); pine(-8.6, -6.2, 1.05); pine(11.6, 6.6, 0.9);
      pine(9.4, -7.2, 0.8); bush(2.6, 5.2, 0.42); bush(3.4, 5.6, 0.34);
      glow(6.6, 2.0, 0, 9, 9); glow(8.4, 1.6, 2.4, 4); glow(0.8, 1.6, 6.0, 4);
      lamp(5.9, 4.4); lamp(5.9, 7.0); lamp(8.9, -3.2, 0.3);
      const WX = -0.05, s1 = (7.2 + 0.28 * Math.cos(G.a) - 2.87) / Math.sin(G.a);         // unde panta ajunge la acoperișul anexei
      const s2 = (7.2 + 0.28 * Math.cos(G.a) - 2.45) / Math.sin(G.a);
      return {
        pv: [G.pt(1, -0.6, 4.4), G.pt(1, -0.6, s1), [-0.6, 2.87, 4.8], [WX, 2.87, 4.8], [WX, 2.13, 4.8]],
        bat: [[WX, 1.07, 4.8], [WX, 0.85, 4.8], [WX, 0.85, 5.25]],
        load: [[WX, 2.13, 4.4], [WX, 2.45, 4.4], [WX, 2.45, G.pt(1, WX, s2)[2] + 0.05]],
        wire, look: [0.8, 2.4, 0.8],
        anch: { pv: [0, 6.4, 2.2], load: [6.4, 3.4, 0.4], inv: [-0.2, 3.3, 4.8], bat: [0.9, 0.0, 8.2] },
      };
    },
  };
  const built = (MODELS[model] || MODELS.clasic)();
  LOOK.set(...built.look); placeCamera();
  const FLOW = { pv: conduit(built.pv), bat: conduit(built.bat, 0.2), load: conduit(built.load),
                 grid: new THREE.CatmullRomCurve3(built.wire) };   // cablul aerian
  const flows = {};
  KIND = null;
  for (const [k, curve] of Object.entries(FLOW)) {
    const tubeMat = new THREE.MeshBasicMaterial({ color: COL[k], transparent: true, opacity: 0.15, depthWrite: false });
    add(new THREE.TubeGeometry(curve, 120, 0.07, 8), tubeMat, 0, 0, 0, { cast: false, receive: false });
    const dots = [];
    for (let i = 0; i < 7; i++) {
      const m = new THREE.Mesh(new THREE.SphereGeometry(0.12, 16, 12), new THREE.MeshBasicMaterial({ color: new THREE.Color(COL[k]).multiplyScalar(1.8) }));
      m.visible = false; scene.add(m); dots.push(m);
    }
    flows[k] = { curve, tubeMat, dots, dir: 0 };
  }

  // ---- strălucirea
  const composer = new EffectComposer(renderer);
  composer.addPass(new RenderPass(scene, camera));
  const bloom = new UnrealBloomPass(new THREE.Vector2(W, H), 0.7, 0.4, 1.6);
  composer.addPass(bloom);
  composer.addPass(new OutputPass());

  // ---- zi / noapte
  let night = null;
  const setNight = (n) => {
    if (night === n) return;
    night = n;
    const g = sky.getContext("2d"), gr = g.createLinearGradient(0, 0, 0, 256);
    if (n) { gr.addColorStop(0, "#071019"); gr.addColorStop(0.55, "#0d2030"); gr.addColorStop(1, "#14283a"); }
    else { gr.addColorStop(0, "#2d6f9e"); gr.addColorStop(0.55, "#7fb4d6"); gr.addColorStop(1, "#cfe2ec"); }
    g.fillStyle = gr; g.fillRect(0, 0, 4, 256); skyTex.needsUpdate = true;
    scene.fog.color.set(n ? 0x0d2030 : 0xbcd6e4);
    for (const [m, c] of Object.values(n ? NIGHT : DAY)) m.color.set(c);
    mat.glass.emissiveIntensity = n ? 1.15 : 0;
    mat.water.emissiveIntensity = n ? 0.55 : 0;
    hemi.color.set(n ? 0x4a6a9a : 0xdfeefe); hemi.groundColor.set(n ? 0x0b1410 : 0x4d6b3a); hemi.intensity = n ? 0.35 : 0.8;
    sun.color.set(n ? 0x9fb8ff : 0xfff1d6); sun.intensity = n ? 0.35 : 2.3;
    scene.environmentIntensity = n ? 0.15 : 0.55;
    renderer.toneMappingExposure = n ? 0.9 : 0.92;
    bloom.strength = n ? 0.5 : 0.7; bloom.threshold = n ? 2.2 : 1.6;
    nightObjs.forEach((o) => (o.visible = n));
  };
  setNight(false);

  // ---- atingerea: raza din cameră → prima piesă cu „tip”
  const ray = new THREE.Raycaster(), ndc = new THREE.Vector2();
  let downAt = null;
  const onDown = (e) => { downAt = [e.clientX, e.clientY]; };
  const onUp = (e) => {
    if (!downAt || Math.hypot(e.clientX - downAt[0], e.clientY - downAt[1]) > 8) return;
    const r = renderer.domElement.getBoundingClientRect();
    ndc.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    ray.setFromCamera(ndc, camera);
    const hit = ray.intersectObjects(scene.children, true).find((h) => h.object.userData.kind !== undefined && h.object.visible);
    onPick(hit?.object.userData.kind || null);
  };
  renderer.domElement.addEventListener("pointerdown", onDown);
  renderer.domElement.addEventListener("pointerup", onUp);
  const lost = (e) => { e.preventDefault(); alive = false; onLost(); };
  renderer.domElement.addEventListener("webglcontextlost", lost);

  // ---- unde apare fiecare element pe ecran (pentru etichetele HTML)
  const ANCH = Object.fromEntries(Object.entries({ grid: [POLE.x, 8.6, POLE.z], ...built.anch }).map(([k, p]) => [k, V(...p)]));
  const anchors = () => Object.fromEntries(Object.entries(ANCH).map(([k, p]) => {
    const v = p.clone().project(camera);
    return [k, [(v.x * 0.5 + 0.5) * W, (-v.y * 0.5 + 0.5) * H]];
  }));

  const resize = () => {
    const w = host.clientWidth, h = host.clientHeight;
    if (!w || !h || (w === W && h === H)) return;
    W = w; H = h;
    renderer.setSize(W, H); composer.setSize(W, H); bloom.resolution.set(W, H);
    camera.aspect = W / H; camera.updateProjectionMatrix();
    if (!moved) placeCamera();                                    // vederea aleasă de utilizator rămâne
  };
  const ro = new ResizeObserver(resize); ro.observe(host);

  let raf = 0, alive = true;
  const clock = new THREE.Clock();
  const frame = () => {
    if (!alive) return;
    raf = requestAnimationFrame(frame);
    if (document.hidden || !host.offsetParent) return;          // ascuns (alt ecran / altă aplicație): nu desenăm
    const t = clock.getElapsedTime();
    for (const f of Object.values(flows)) {
      f.dots.forEach((m, i) => {
        m.visible = f.dir !== 0;
        if (!f.dir) return;
        let u = (i / f.dots.length + t * 0.32) % 1; if (f.dir < 0) u = 1 - u;
        m.position.copy(f.curve.getPointAt(u)); m.scale.setScalar(0.75 + 0.35 * Math.sin(u * Math.PI));
      });
    }
    ledMat.color.setHex(0x3ddc84).multiplyScalar(2 + 1.5 * Math.sin(t * 4));
    controls.update();
    composer.render();
    onFrame(anchors());
  };
  frame();

  return {
    // night: true/false; flows: {pv, bat, load, grid} = 1 (spre invertor/consumator), -1 (invers), 0 (oprit); soc 0–100
    set({ night: n, flows: fl = {}, soc } = {}) {
      if (n !== undefined) setNight(!!n);
      for (const [k, d] of Object.entries(fl)) if (flows[k]) {
        flows[k].dir = d; flows[k].tubeMat.opacity = d ? 0.45 : 0.12;
      }
      if (soc != null) batLeds.forEach((m, i) => m.color.set(soc > i * 25 + 5 ? 0x3ddc84 : 0x56656f));
    },
    anchors,
    resetView() { moved = false; placeCamera(); onMove(false); },   // vederea de la început
    // pentru capturi și verificări: camera într-un punct anume, privind spre altul
    setView(pos, target) { camera.position.set(...pos); controls.target.set(...target); controls.update(); },
    dispose() {
      alive = false; cancelAnimationFrame(raf); ro.disconnect(); controls.dispose();
      renderer.domElement.removeEventListener("pointerdown", onDown);
      renderer.domElement.removeEventListener("pointerup", onUp);
      scene.traverse((o) => { o.geometry?.dispose(); (Array.isArray(o.material) ? o.material : [o.material]).forEach((m) => m?.dispose?.()); });
      renderer.domElement.removeEventListener("webglcontextlost", lost);
      composer.dispose?.(); pmrem.dispose(); renderer.dispose();
      renderer.forceContextLoss(); renderer.domElement.remove();
    },
  };
}
