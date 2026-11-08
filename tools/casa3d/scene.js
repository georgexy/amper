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

const COL = { pv: 0xffb627, bat: 0x3ddc84, load: 0xa98bff, grid: 0x45b4ff };

export function supported() {
  try {
    const c = document.createElement("canvas");
    return !!(window.WebGLRenderingContext && (c.getContext("webgl2") || c.getContext("webgl")));
  } catch { return false; }
}

export function mount(host, { onPick = () => {}, onFrame = () => {} } = {}) {
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
  const placeCamera = () => {
    // pe ecrane late, camera mai aproape; pe telefon (portret) mai departe, ca să încapă toată insula
    const f = Math.min(1.25, Math.max(0.78, 0.46 / (W / H)));
    camera.position.set(36 * f, 36 * f, 41 * f);
    camera.lookAt(LOOK);
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
  };
  const DAY = {}, NIGHT = {};
  const tone = (m, day, night) => { DAY[m.uuid] = [m, day]; NIGHT[m.uuid] = [m, night]; };
  tone(mat.plaster, 0xf3f1ec, 0xb9bec2); tone(mat.plaster2, 0xe2e3e0, 0x9aa1a7); tone(mat.grass, 0x86b86a, 0x23402c);
  tone(mat.soil, 0xb9a58a, 0x2a2723); tone(mat.stone, 0xd9d6cf, 0x6d7175); tone(mat.leaf, 0x5fa35a, 0x2e5a3c);
  tone(mat.leaf2, 0x7cc06c, 0x3a6e4a); tone(mat.garage, 0x9aa4ac, 0x4a525a); tone(mat.glass, 0x1d3b52, 0xffc477);

  // panourile: textură cu celule
  const pc = document.createElement("canvas"); pc.width = 256; pc.height = 416;
  { const g = pc.getContext("2d"); const gr = g.createLinearGradient(0, 0, 256, 416);
    gr.addColorStop(0, "#1f4f86"); gr.addColorStop(1, "#0d2445"); g.fillStyle = gr; g.fillRect(0, 0, 256, 416);
    g.strokeStyle = "rgba(170,205,240,.45)"; g.lineWidth = 2;
    for (let i = 1; i < 6; i++) { g.beginPath(); g.moveTo(i * 256 / 6, 0); g.lineTo(i * 256 / 6, 416); g.stroke(); }
    for (let j = 1; j < 10; j++) { g.beginPath(); g.moveTo(0, j * 416 / 10); g.lineTo(256, j * 416 / 10); g.stroke(); } }
  const panTex = new THREE.CanvasTexture(pc); panTex.colorSpace = THREE.SRGBColorSpace; panTex.anisotropy = 8;
  const panelMat = new THREE.MeshPhysicalMaterial({ map: panTex, roughness: 0.32, metalness: 0.15, clearcoat: 0.6,
    clearcoatRoughness: 0.25, envMapIntensity: 0.55 });

  let KIND = null;
  const add = (geo, m, x, y, z, o = {}) => {
    const mesh = new THREE.Mesh(geo, m); mesh.position.set(x, y, z); mesh.userData.kind = KIND;
    mesh.castShadow = o.cast ?? true; mesh.receiveShadow = o.receive ?? true;
    if (o.rot) mesh.rotation.set(...o.rot);
    (o.parent || scene).add(mesh); return mesh;
  };
  const box = (w, h, d, m, x, y, z, o) => add(new THREE.BoxGeometry(w, h, d), m, x, y, z, o);
  const rbox = (w, h, d, r, m, x, y, z, o) => add(new RoundedBoxGeometry(w, h, d, 4, r), m, x, y, z, o);

  // ---- terenul: o insulă cu margini rotunjite, alee și terasă
  rbox(24, 1.2, 19, 0.5, mat.soil, 1, -0.62, 0.5, { cast: false });
  rbox(23.6, 0.3, 18.6, 0.4, mat.grass, 1, -0.05, 0.5, { cast: false });
  box(2.2, 0.06, 6.5, mat.stone, 2.0, 0.12, 6.4, { cast: false });
  box(10.2, 0.1, 2.0, mat.stone, -0.3, 0.12, 4.3, { cast: false });

  // ---- casa: parter, etaj în consolă, acoperiș plat cu atic, lemn, geamuri; garaj în dreapta
  KIND = "load";
  const HX = -0.5, HZ = 0;
  box(9, 3.0, 7, mat.plaster, HX, 1.6, HZ);
  box(9.8, 3.0, 7.6, mat.plaster, HX + 0.3, 4.7, HZ + 0.2);
  box(10.2, 0.32, 8.0, mat.roof, HX + 0.3, 6.25, HZ + 0.2);
  for (const [w, d, x, z] of [[10.2, 0.2, 0, -3.8], [10.2, 0.2, 0, 4.2], [0.2, 8, -4.8, 0.2], [0.2, 8, 5.4, 0.2]])
    box(w, 0.5, d, mat.plaster2, HX + 0.3 + x, 6.6, z);
  for (let i = 0; i < 16; i++) box(0.12, 2.9, 0.08, mat.wood, HX + 1.2 + i * 0.22, 4.6, HZ + 4.04, { cast: false });
  const win = (w, h, x, y, z, face = "z") => {
    if (face === "z") { box(w + 0.14, h + 0.14, 0.1, mat.trim, x, y, z); box(w, h, 0.06, mat.glass, x, y, z + 0.04); }
    else { box(0.1, h + 0.14, w + 0.14, mat.trim, x, y, z); box(0.06, h, w, mat.glass, x + 0.04, y, z); }
  };
  win(3.2, 2.2, HX - 2.2, 4.7, HZ + 4.02); win(5.0, 2.3, HX + 0.6, 1.55, HZ + 3.53); win(1.6, 2.0, HX - 3.4, 1.55, HZ + 3.53);
  win(2.4, 1.6, HX + 5.22, 4.7, HZ - 1.2, "x"); win(2.4, 1.6, HX + 5.22, 4.7, HZ + 2.0, "x"); win(3.0, 2.0, HX + 4.52, 1.6, HZ + 0.4, "x");
  box(1.2, 2.5, 0.1, mat.wood, HX + 3.5, 1.35, HZ + 3.53);
  const GX = 6.6, GZ = 0.6;
  box(4.4, 3.0, 6.0, mat.plaster2, GX, 1.6, GZ);
  box(4.7, 0.25, 6.3, mat.roof, GX, 3.22, GZ);
  box(3.2, 2.3, 0.08, mat.garage, GX - 0.1, 1.27, GZ + 3.02);
  for (let i = 0; i < 6; i++) box(3.2, 0.03, 0.04, mat.trim, GX - 0.1, 0.35 + i * 0.38, GZ + 3.07, { cast: false });

  // ---- panourile: 3 rânduri × 4, pe suporturi de aluminiu
  KIND = "pv";
  for (let r = 0; r < 3; r++) for (let c = 0; c < 4; c++) {
    const x = HX - 3.0 + c * 2.15, z = HZ - 2.4 + r * 2.35, y = 6.85;
    add(new THREE.BoxGeometry(2.0, 0.05, 1.7), panelMat, x, y + 0.42, z, { rot: [0.28, 0, 0] });
    add(new THREE.BoxGeometry(2.06, 0.06, 1.76), mat.alu, x, y + 0.4, z, { rot: [0.28, 0, 0] });
    box(0.06, 0.55, 0.06, mat.alu, x - 0.9, y + 0.15, z - 0.6); box(0.06, 0.55, 0.06, mat.alu, x + 0.9, y + 0.15, z - 0.6);
  }
  // ---- invertorul, pe peretele garajului
  KIND = "inv";
  const INV = new THREE.Vector3(GX + 2.26, 1.75, GZ - 0.9);
  rbox(0.22, 1.05, 0.8, 0.06, mat.white, INV.x, INV.y, INV.z);
  box(0.03, 0.22, 0.5, mat.dark, INV.x + 0.11, INV.y + 0.22, INV.z, { cast: false });
  const ledMat = new THREE.MeshBasicMaterial({ color: 0x3ddc84 });
  box(0.03, 0.04, 0.28, ledMat, INV.x + 0.115, INV.y - 0.2, INV.z, { cast: false });
  // ---- bateria: turn de module; benzile verzi arată cât e încărcată
  KIND = "bat";
  const BAT = new THREE.Vector3(GX + 2.55, 0, GZ + 1.1);
  const batLeds = [];
  for (let i = 0; i < 4; i++) {
    rbox(0.55, 0.42, 0.95, 0.05, mat.white, BAT.x, 0.32 + i * 0.45, BAT.z);
    const m = new THREE.MeshBasicMaterial({ color: 0x3ddc84 });
    batLeds.push(m); box(0.02, 0.05, 0.6, m, BAT.x + 0.28, 0.32 + i * 0.45, BAT.z, { cast: false });
  }
  rbox(0.6, 0.12, 1.0, 0.04, mat.dark, BAT.x, 0.14, BAT.z);
  // ---- stâlpul rețelei și cablul spre casă
  KIND = "grid";
  const POLE = new THREE.Vector3(-10.0, 0, -1.5);
  add(new THREE.CylinderGeometry(0.12, 0.16, 8.4, 12), mat.pole, POLE.x, 4.2, POLE.z);
  box(2.0, 0.12, 0.12, mat.pole, POLE.x, 7.9, POLE.z);
  const wirePts = [new THREE.Vector3(POLE.x, 7.85, POLE.z), new THREE.Vector3(-7.6, 6.7, -1.0), new THREE.Vector3(HX - 4.95, 5.6, HZ - 0.5)];
  add(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(wirePts), 24, 0.025, 6), mat.dark, 0, 0, 0, { cast: false });
  // ---- copaci, tufe, gard
  KIND = null;
  const tree = (x, z, s = 1) => {
    add(new THREE.CylinderGeometry(0.12 * s, 0.16 * s, 1.8 * s, 8), mat.trunk, x, 0.9 * s, z);
    add(new THREE.IcosahedronGeometry(1.1 * s, 1), mat.leaf, x, 2.2 * s, z);
    add(new THREE.IcosahedronGeometry(0.8 * s, 1), mat.leaf2, x + 0.5 * s, 2.7 * s, z + 0.3 * s);
    add(new THREE.IcosahedronGeometry(0.7 * s, 1), mat.leaf2, x - 0.5 * s, 2.5 * s, z - 0.2 * s);
  };
  tree(-8.2, 6.6, 1.15); tree(10.8, -5.6, 1.0); tree(-8.6, -6.2, 0.95); tree(11.0, 7.4, 0.75);
  for (const [x, z, s] of [[-3.6, 5.2, 0.5], [-2.6, 5.4, 0.42], [5.2, 5.6, 0.45], [-5.4, 3.8, 0.4]])
    add(new THREE.IcosahedronGeometry(s, 1), mat.leaf2, x, s * 0.7, z);
  for (let i = 0; i < 18; i++) box(0.08, 0.55, 0.08, mat.white, -10.5 + i * 0.75, 0.38, 9.4, { cast: false });
  box(13.0, 0.06, 0.06, mat.white, -4.1, 0.55, 9.4, { cast: false });

  // ---- noaptea: lumina din casă, felinarele de pe alee, stelele
  const nightObjs = [];
  for (const [x, y, z] of [[HX + 0.6, 1.6, HZ + 5.2], [HX - 2.2, 4.7, HZ + 5.4], [HX + 6.2, 4.7, HZ + 0.4]]) {
    const l = new THREE.PointLight(0xffb55e, 7, 8, 1.6); l.position.set(x, y, z); scene.add(l); nightObjs.push(l);
  }
  const lampMat = new THREE.MeshBasicMaterial({ color: new THREE.Color(0xffd28a).multiplyScalar(4) });
  for (const z of [4.6, 6.6, 8.6]) {
    box(0.08, 0.7, 0.08, mat.dark, 3.4, 0.45, z, { cast: false });
    const bulb = add(new THREE.SphereGeometry(0.12, 12, 8), lampMat, 3.4, 0.85, z, { cast: false });
    const l = new THREE.PointLight(0xffc77a, 3, 3.5, 1.8); l.position.set(3.4, 0.9, z); scene.add(l);
    nightObjs.push(l, bulb);
  }
  const starGeo = new THREE.BufferGeometry(), sp = [];
  for (let i = 0; i < 300; i++) sp.push(-30 + (Math.random() - 0.5) * 120, 20 + Math.random() * 50, -40 + (Math.random() - 0.5) * 60);
  starGeo.setAttribute("position", new THREE.Float32BufferAttribute(sp, 3));
  const stars = new THREE.Points(starGeo, new THREE.PointsMaterial({ color: 0xcfe3f2, size: 0.35, fog: false }));
  scene.add(stars); nightObjs.push(stars);

  // ---- fluxurile: un tub discret și particule luminoase pe el
  const FLOW = {
    pv: [[HX + 3.5, 7.0, HZ + 1.9], [HX + 4.9, 6.45, HZ + 3.0], [GX - 0.6, 3.45, GZ + 2.5], [GX + 2.2, 3.4, GZ - 0.9], [INV.x + 0.05, INV.y + 0.6, INV.z]],
    bat: [[INV.x + 0.12, INV.y - 0.45, INV.z + 0.2], [INV.x + 0.25, 1.4, GZ + 0.2], [BAT.x, 1.95, BAT.z - 0.1]],
    load: [[INV.x + 0.12, INV.y - 0.3, INV.z + 0.42], [GX + 2.28, 2.6, GZ + 2.4], [GX + 1.8, 2.7, GZ + 3.1], [GX - 2.1, 2.7, GZ + 3.1], [HX + 4.2, 2.2, HZ + 3.65], [HX + 2.4, 1.8, HZ + 3.62]],
    grid: wirePts.map((v) => v.toArray()),
  };
  const flows = {};
  KIND = null;
  for (const [k, pts] of Object.entries(FLOW)) {
    const curve = new THREE.CatmullRomCurve3(pts.map((p) => new THREE.Vector3(...p)), false, "centripetal");
    const tubeMat = new THREE.MeshBasicMaterial({ color: COL[k], transparent: true, opacity: 0.15, depthWrite: false });
    add(new THREE.TubeGeometry(curve, 120, 0.07, 8), tubeMat, 0, 0, 0, { cast: false, receive: false });
    const dots = [];
    for (let i = 0; i < 7; i++) {
      const m = new THREE.Mesh(new THREE.SphereGeometry(0.15, 16, 12), new THREE.MeshBasicMaterial({ color: new THREE.Color(COL[k]).multiplyScalar(5) }));
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

  // ---- unde apare fiecare element pe ecran (pentru etichetele HTML)
  const ANCH = {
    pv: new THREE.Vector3(HX + 1.5, 8.6, HZ - 1.5), grid: new THREE.Vector3(POLE.x, 8.6, POLE.z),
    load: new THREE.Vector3(HX - 1.0, 2.4, HZ + 5.6), inv: new THREE.Vector3(GX - 0.6, 4.1, GZ - 1.2),
    bat: new THREE.Vector3(BAT.x + 0.6, 0.0, BAT.z + 4.2),
  };
  const anchors = () => Object.fromEntries(Object.entries(ANCH).map(([k, p]) => {
    const v = p.clone().project(camera);
    return [k, [(v.x * 0.5 + 0.5) * W, (-v.y * 0.5 + 0.5) * H]];
  }));

  const resize = () => {
    const w = host.clientWidth, h = host.clientHeight;
    if (!w || !h || (w === W && h === H)) return;
    W = w; H = h;
    renderer.setSize(W, H); composer.setSize(W, H); bloom.resolution.set(W, H);
    camera.aspect = W / H; camera.updateProjectionMatrix(); placeCamera();
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
    dispose() {
      alive = false; cancelAnimationFrame(raf); ro.disconnect();
      renderer.domElement.removeEventListener("pointerdown", onDown);
      renderer.domElement.removeEventListener("pointerup", onUp);
      scene.traverse((o) => { o.geometry?.dispose(); (Array.isArray(o.material) ? o.material : [o.material]).forEach((m) => m?.dispose?.()); });
      composer.dispose?.(); pmrem.dispose(); renderer.dispose(); renderer.domElement.remove();
    },
  };
}
