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
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";

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

export function mount(host, { onPick = () => {}, onFrame = () => {}, onLost = () => {}, onMove = () => {} } = {}) {
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
  controls.minDistance = 10; controls.maxDistance = 95;
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
    field: M({ color: 0x7aa95f, roughness: 1 }), forest: M({ color: 0x5b8547, roughness: 1 }),
    asphalt: M({ color: 0x3b4046, roughness: 0.95 }), walk: M({ color: 0xc9c6bf, roughness: 0.95 }),
    paint: M({ color: 0xf2f2ee, roughness: 0.8 }), conifer: M({ color: 0x3f7a4a, roughness: 0.9, flatShading: true }),
    car: M({ color: 0xb8432f, roughness: 0.35, metalness: 0.5 }), tile: M({ color: 0x9b4a35, roughness: 0.85 }),
  };
  const DAY = {}, NIGHT = {};
  const tone = (m, day, night) => { DAY[m.uuid] = [m, day]; NIGHT[m.uuid] = [m, night]; };
  tone(mat.plaster, 0xf3f1ec, 0xb9bec2); tone(mat.plaster2, 0xe2e3e0, 0x9aa1a7); tone(mat.grass, 0x86b86a, 0x23402c);
  tone(mat.soil, 0xb9a58a, 0x2a2723); tone(mat.stone, 0xd9d6cf, 0x6d7175); tone(mat.leaf, 0x5fa35a, 0x2e5a3c);
  tone(mat.leaf2, 0x7cc06c, 0x3a6e4a); tone(mat.garage, 0x9aa4ac, 0x4a525a); tone(mat.glass, 0x1d3b52, 0xffc477);
  tone(mat.field, 0x7aa95f, 0x1f3a28); tone(mat.forest, 0x5b8547, 0x172c1e); tone(mat.asphalt, 0x3b4046, 0x15181b);
  tone(mat.walk, 0xc9c6bf, 0x4e5154); tone(mat.paint, 0xf2f2ee, 0x6a6c6e); tone(mat.conifer, 0x3f7a4a, 0x173323);
  tone(mat.tile, 0x9b4a35, 0x3b2019); tone(mat.car, 0xb8432f, 0x4a1d15);

  // panourile: textură cu celule
  const pc = document.createElement("canvas"); pc.width = 256; pc.height = 416;
  { const g = pc.getContext("2d"); const gr = g.createLinearGradient(0, 0, 256, 416);
    gr.addColorStop(0, "#1f4f86"); gr.addColorStop(1, "#0d2445"); g.fillStyle = gr; g.fillRect(0, 0, 256, 416);
    g.strokeStyle = "rgba(170,205,240,.45)"; g.lineWidth = 2;
    for (let i = 1; i < 6; i++) { g.beginPath(); g.moveTo(i * 256 / 6, 0); g.lineTo(i * 256 / 6, 416); g.stroke(); }
    for (let j = 1; j < 10; j++) { g.beginPath(); g.moveTo(0, j * 416 / 10); g.lineTo(256, j * 416 / 10); g.stroke(); } }
  const panTex = new THREE.CanvasTexture(pc); panTex.colorSpace = THREE.SRGBColorSpace; panTex.anisotropy = 8;
  // mat: reflexia soarelui pe panouri „ardea” imaginea din unele unghiuri
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

  // ---- terenul: un câmp mare care se pierde în ceață (cerut 2026-11-19: „să nu văd sub pământ”), curtea cu gazon,
  // alee și terasă; strada, vecinii și pădurea sunt mai jos („împrejurimile”)
  add(new THREE.CircleGeometry(260, 72), mat.field, 0, 0, 0, { cast: false, rot: [-Math.PI / 2, 0, 0] });
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

  // ---- împrejurimile (cerute 2026-11-19): strada din față, vecinii de peste drum, pădurea din spate
  const rng = (() => { let x = 11; return () => ((x = (x * 16807) % 2147483647) / 2147483647); })();   // mereu la fel
  const SZ = 14.6;                                                      // mijlocul străzii
  box(320, 0.04, 6.2, mat.asphalt, 0, 0.02, SZ, { cast: false });
  box(320, 0.14, 1.9, mat.walk, 0, 0.07, SZ - 4.05, { cast: false });   // trotuarul nostru
  box(320, 0.14, 1.9, mat.walk, 0, 0.07, SZ + 4.05, { cast: false });
  {                                                                      // linia punctată din mijloc
    const n = 80, dash = new THREE.InstancedMesh(new THREE.BoxGeometry(2.2, 0.02, 0.14), mat.paint, n), m4 = new THREE.Matrix4();
    for (let i = 0; i < n; i++) dash.setMatrixAt(i, m4.makeTranslation(-158 + i * 4, 0.05, SZ));
    scene.add(dash);
  }
  box(3.4, 0.05, 6.3, mat.stone, GX - 0.1, 0.11, 6.75, { cast: false });  // aleea garajului, până la trotuar
  box(2.2, 0.05, 1.2, mat.stone, 2.0, 0.11, 10.1, { cast: false });       // aleea casei, până la trotuar
  // o mașină parcată pe aleea garajului
  rbox(1.8, 0.62, 4.2, 0.22, mat.car, GX - 0.1, 0.55, 7.4);
  rbox(1.6, 0.55, 2.2, 0.22, mat.car, GX - 0.1, 1.08, 7.2);
  box(1.5, 0.42, 0.06, mat.glass, GX - 0.1, 1.1, 8.3, { cast: false }); box(1.5, 0.42, 0.06, mat.glass, GX - 0.1, 1.1, 6.1, { cast: false });
  for (const [dx, dz] of [[-0.9, 6.0], [0.9, 6.0], [-0.9, 8.8], [0.9, 8.8]])
    add(new THREE.CylinderGeometry(0.34, 0.34, 0.24, 14), mat.dark, GX - 0.1 + dx, 0.34, dz, { rot: [0, 0, Math.PI / 2] });
  // felinarele străzii (noaptea se aprind; doar cele două din dreptul casei luminează cu adevărat)
  const lampOn = new THREE.MeshBasicMaterial({ color: new THREE.Color(0xffe0a8).multiplyScalar(3) });
  const streetLamps = [];
  for (const x of [-34, -14, 18, 38]) {
    add(new THREE.CylinderGeometry(0.07, 0.1, 4.6, 8), mat.pole, x, 2.3, SZ - 3.4);
    box(0.08, 0.08, 1.2, mat.pole, x, 4.55, SZ - 2.85);
    streetLamps.push(add(new THREE.BoxGeometry(0.34, 0.1, 0.5), lampOn, x, 4.48, SZ - 2.3, { cast: false }));
  }
  // vecinii de peste drum: case simple cu acoperiș în două ape, gazon, gard viu și copaci
  const roofGeo = new THREE.CylinderGeometry(1, 1, 1, 3, 1);           // prismă triunghiulară
  const roofBasis = new THREE.Matrix4().makeBasis(new THREE.Vector3(0, 0, 1), new THREE.Vector3(1, 0, 0), new THREE.Vector3(0, 1, 0));
  const neighbor = (x, z, w, d, h, wall) => {
    const m = M({ color: wall, roughness: 0.92 }); tone(m, wall, new THREE.Color(wall).multiplyScalar(0.55).getHex());
    rbox(w + 3, 0.12, d + 6, 0.2, mat.grass, x, 0.04, z, { cast: false });
    box(w, h, d, m, x, h / 2, z);
    // prisma: axa (y local) de-a lungul casei (x), vârful triunghiului (z local) în sus; baza = adâncimea casei
    const sz = (0.42 * d) / 1.5;
    const r = add(roofGeo, mat.tile, x, h + 0.5 * sz, z);
    r.quaternion.setFromRotationMatrix(roofBasis);
    r.scale.set((d / 2 + 0.35) / 0.866, w + 0.6, sz);
    for (let i = 0; i < Math.floor(w / 2.6); i++) box(1.1, 1.2, 0.06, mat.glass, x - w / 2 + 1.4 + i * 2.6, h * 0.55, z - d / 2 - 0.03, { cast: false });
    box(1.0, 2.0, 0.06, mat.wood, x + w / 2 - 1.3, 1.0, z - d / 2 - 0.03, { cast: false });
    box(w + 2.6, 0.9, 0.6, mat.leaf, x, 0.45, z - d / 2 - 2.6, { cast: false });       // gard viu spre stradă
  };
  neighbor(-26, 27, 9, 7, 3.2, 0xe9dfcc); neighbor(-6, 28, 8, 7.5, 3.0, 0xdbe3e6); neighbor(14, 27, 10, 7, 3.4, 0xf1e7d6);
  neighbor(34, 28, 8, 7, 3.0, 0xd3dccf);
  for (const [x, z, sc] of [[-36, 24, 1.2], [-16, 25, 1.0], [4, 24, 1.1], [24, 25, 0.9], [43, 24, 1.15], [-17, 33, 1.3], [26, 34, 1.2]])
    tree(x, z, sc);
  // pădurea din spate (și pe laturi, mai rară): conifere și foioase, ca instanțe (o singură desenare pe tip)
  add(new THREE.PlaneGeometry(220, 80), mat.forest, 0, 0.01, -52, { cast: false, rot: [-Math.PI / 2, 0, 0] });
  {
    const spots = [];
    for (let i = 0; i < 900 && spots.length < 340; i++) {
      const x = -110 + rng() * 220, z = -95 + rng() * 85;
      const inside = z > -13 && x > -16 && x < 18;                      // curtea și imediat în spatele ei rămân libere
      const sides = z > -13 && (x < -16 || x > 18);
      if (inside || z > 6 || (sides && rng() < 0.75) || (z > -16 && rng() < 0.5)) continue;
      spots.push([x, z, 0.75 + rng() * 0.7, rng()]);
    }
    const coneG = new THREE.ConeGeometry(1.5, 4.6, 7), trunkG = new THREE.CylinderGeometry(0.16, 0.22, 1.4, 6);
    const crownG = new THREE.IcosahedronGeometry(1.6, 1);
    const con = spots.filter((p) => p[3] < 0.7), dec = spots.filter((p) => p[3] >= 0.7);
    const cones = new THREE.InstancedMesh(coneG, mat.conifer, con.length), crowns = new THREE.InstancedMesh(crownG, mat.leaf, dec.length);
    const trunks = new THREE.InstancedMesh(trunkG, mat.trunk, spots.length);
    const m4 = new THREE.Matrix4(), q = new THREE.Quaternion(), sv = new THREE.Vector3(), pv = new THREE.Vector3(), col = new THREE.Color();
    con.forEach(([x, z, sc], i) => {
      cones.setMatrixAt(i, m4.compose(pv.set(x, 1.2 * sc + 2.3 * sc, z), q, sv.set(sc, sc, sc)));
      cones.setColorAt(i, col.setHSL(0.36 + rng() * 0.05, 0.35, 0.72 + rng() * 0.28));
    });
    dec.forEach(([x, z, sc], i) => {
      crowns.setMatrixAt(i, m4.compose(pv.set(x, 1.4 * sc + 1.3 * sc, z), q, sv.set(sc, sc * 0.9, sc)));
      crowns.setColorAt(i, col.setHSL(0.27 + rng() * 0.08, 0.4, 0.8 + rng() * 0.2));
    });
    spots.forEach(([x, z, sc], i) => trunks.setMatrixAt(i, m4.compose(pv.set(x, 0.7 * sc, z), q, sv.set(sc, sc, sc))));
    for (const im of [cones, crowns, trunks]) { im.castShadow = false; im.receiveShadow = false; scene.add(im); }
  }

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
  for (const x of [-14, 18]) {
    const l = new THREE.PointLight(0xffd9a0, 9, 11, 1.6); l.position.set(x, 4.2, SZ - 2.3); scene.add(l); nightObjs.push(l);
  }
  const lampOff = M({ color: 0x8a8f94, roughness: 0.5 });
  const starGeo = new THREE.BufferGeometry(), sp = [];
  for (let i = 0; i < 300; i++) sp.push(-30 + (Math.random() - 0.5) * 120, 20 + Math.random() * 50, -40 + (Math.random() - 0.5) * 60);
  starGeo.setAttribute("position", new THREE.Float32BufferAttribute(sp, 3));
  const stars = new THREE.Points(starGeo, new THREE.PointsMaterial({ color: 0xcfe3f2, size: 0.35, fog: false }));
  scene.add(stars); nightObjs.push(stars);

  // ---- norii (cerute 2026-11-19: „când e înnorat afară, să fie înnorat și acolo”, apoi „norii pe tot cerul”):
  // câte un nor = sfere turtite unite într-o singură formă (o desenare); trei grupuri, câți apar din fiecare după
  // acoperirea cerului din prognoză:
  //  - `field`: stratul de sus, peste toată zona (până la 170 m), la 30–46 m: se vede când ridici privirea spre cer;
  //  - `ring`: spre orizont (120–190 m), mai jos: cerul plin de nori când privești în depărtare;
  //  - `spots`: cei din vederea de la început (deasupra pădurii), puși pe raza camerei prin locuri goale de pe ecran.
  // Un nor ajuns pe ecran în dreptul casei sau prea aproape de cameră se estompează, ca să nu o acopere.
  const cloudMat = M({ color: 0xffffff, roughness: 1, flatShading: true, transparent: true, opacity: 0.94 });
  const puff = new THREE.IcosahedronGeometry(1, 2);
  const rnd = (() => { let x = 7; return () => ((x = (x * 16807) % 2147483647) / 2147483647); })();   // aceeași formă mereu
  const cloudGeo = () => {
    const n = 5 + Math.floor(rnd() * 4), len = 4 + rnd() * 3.5, parts = [];
    for (let j = 0; j < n; j++) {
      const r = 1.25 + rnd() * 1.2, g = puff.clone();
      g.scale(r * 1.25, r * 0.72, r);
      g.translate((j / (n - 1) - 0.5) * len, rnd() * 1.1 + (1 - Math.abs(j / (n - 1) - 0.5) * 2) * 1.0, (rnd() - 0.5) * 2.6);
      parts.push(g);
    }
    return mergeGeometries(parts);
  };
  const SHAPES = Array.from({ length: 8 }, cloudGeo);
  const clouds = { spots: [], field: [], ring: [] };
  const newCloud = (group, pos, scale, data = {}) => {
    const m = new THREE.Mesh(SHAPES[Math.floor(rnd() * SHAPES.length)], cloudMat.clone());
    m.position.copy(pos); m.scale.setScalar(scale); m.rotation.y = rnd() * Math.PI * 2;
    m.castShadow = false; m.receiveShadow = false; m.visible = false;
    m.userData = { speed: 0.4 + rnd() * 0.5, ...data };
    scene.add(m); clouds[group].push(m);
    return m;
  };
  for (let i = 0; i < 46; i++) {
    const r = 20 + Math.sqrt(rnd()) * 150, th = rnd() * Math.PI * 2;
    newCloud("field", new THREE.Vector3(Math.cos(th) * r, 30 + rnd() * 16, Math.sin(th) * r), 1.6 + rnd() * 1.4);
  }
  for (let i = 0; i < 18; i++) {
    const r = 120 + rnd() * 70, th = (i / 18) * Math.PI * 2 + rnd() * 0.25;
    newCloud("ring", new THREE.Vector3(Math.cos(th) * r, 14 + rnd() * 12, Math.sin(th) * r), 2.6 + rnd() * 1.6);
  }
  // locuri goale de pe ecranul de la început (coordonate -1…1, y în sus), deasupra pădurii; norul stă pe raza din
  // cameră prin acel punct, la înălțimea `Y` (m)
  const SPOTS = [[-0.62, 0.72, 16], [0.45, 0.7, 16], [0.92, 0.45, 15], [-0.92, 0.5, 15], [-0.05, 0.8, 17], [0.85, 0.78, 16],
                 [0.15, 0.63, 18], [-0.3, 0.68, 18], [0.62, 0.6, 17], [-0.75, 0.62, 16]];
  camera.updateMatrixWorld();
  const camRight = new THREE.Vector3().setFromMatrixColumn(camera.matrixWorld, 0);
  for (const [nx, ny, Y] of SPOTS) {
    const dir = new THREE.Vector3(nx, ny, 0.5).unproject(camera).sub(camera.position).normalize();
    const d = Math.min(140, (Y - camera.position.y) / Math.min(-0.05, dir.y));
    const base = camera.position.clone().add(dir.multiplyScalar(d));
    const m = newCloud("spots", base, Math.max(0.45, Math.min(1.6, d / 55)),
                       { base, amp: 1.5 + rnd() * 2, w: 0.05 + rnd() * 0.05, ph: rnd() * 6.28 });
    m.lookAt(camera.position.x, base.y, camera.position.z);
  }
  const allClouds = [...clouds.spots, ...clouds.field, ...clouds.ring];
  // vântul: norii de sus și de la orizont trec încet spre dreapta (și o iau de la capăt), cei din vederea de la
  // început doar se legănă (fără salturi)
  const moveCloud = (m, t, dt) => {
    const u = m.userData;
    if (u.base) { m.position.copy(u.base).addScaledVector(camRight, u.amp * Math.sin(t * u.w + u.ph)); return; }
    m.position.x += u.speed * dt;
    if (m.position.x > 190) m.position.x -= 380;
  };
  // umbrele norilor pe pământ: pete întunecate care trec încet peste curte și stradă (doar ziua, cu cer parțial
  // noros; pe cer acoperit soarele nu mai face umbre)
  const shadows = [];
  for (let i = 0; i < 8; i++) {
    const m = new THREE.MeshBasicMaterial({ color: 0x000000, transparent: true, opacity: 0, depthWrite: false });
    const sh = new THREE.Mesh(new THREE.CircleGeometry(1, 28), m);
    sh.rotation.x = -Math.PI / 2;
    sh.scale.set(5 + rnd() * 5, 3.5 + rnd() * 3, 1);
    sh.position.set(-40 + i * 10 + rnd() * 6, 0.16, -18 + rnd() * 36);
    sh.renderOrder = 2; sh.visible = false;
    sh.userData.speed = 0.5 + rnd() * 0.4;
    scene.add(sh); shadows.push(sh);
  }
  let shadowAlpha = 0;

  // ---- ploaia și ninsoarea (cerute 2026-11-19): particule într-un volum de 180 × 60 × 180 m care urmează locul la
  // care te uiți; mișcarea e calculată pe placa video (shader), deci o singură desenare. Câte se văd = intensitatea.
  const PW = 180, PH = 60;
  const precipGeo = (n, line) => {
    const pos = new Float32Array(n * (line ? 6 : 3)), end = new Float32Array(n * (line ? 2 : 1)), seed = new Float32Array(n * (line ? 2 : 1));
    for (let i = 0; i < n; i++) {
      const x = rnd() * PW, y = rnd() * PH, z = rnd() * PW, sd = rnd();
      for (let k = 0; k < (line ? 2 : 1); k++) {
        const j = i * (line ? 2 : 1) + k;
        pos.set([x, y, z], j * 3); end[j] = k; seed[j] = sd;
      }
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    g.setAttribute("aEnd", new THREE.BufferAttribute(end, 1));
    g.setAttribute("aSeed", new THREE.BufferAttribute(seed, 1));
    return g;
  };
  const precipUniforms = () => ({ uTime: { value: 0 }, uCenter: { value: new THREE.Vector3() }, uOpacity: { value: 0.5 },
                                  uColor: { value: new THREE.Color(0xc8d6e4) }, uFade: { value: 100 } });
  // poziția: împachetată în cutia din jurul centrului (picăturile rămân pe loc în lume, cutia le urmează privirea)
  const WRAP = `vec3 wrapPos(vec3 p, float fall) {
      float x = mod(p.x - uCenter.x + ${PW / 2}.0, ${PW}.0) + uCenter.x - ${PW / 2}.0;
      float z = mod(p.z - uCenter.z + ${PW / 2}.0, ${PW}.0) + uCenter.z - ${PW / 2}.0;
      float y = mod(p.y - fall, ${PH}.0);
      return vec3(x, y, z); }`;
  // ploaia: puncte mari în care se desenează o dâră verticală subțire (liniile de 1 pixel dispăreau pe telefoanele
  // cu ecran dens); grosimea dârei crește cu densitatea ecranului
  const PR = renderer.getPixelRatio();
  const rainMat = new THREE.ShaderMaterial({
    uniforms: { ...precipUniforms(), uSize: { value: 2.2 * PR * H / 2 } }, transparent: true, depthWrite: false,
    vertexShader: `uniform float uTime; uniform vec3 uCenter; uniform float uSize; attribute float aSeed; varying float vD;
      ${WRAP}
      void main() {
        vec3 w = wrapPos(position, uTime * (17.0 + aSeed * 6.0));
        vec4 mv = viewMatrix * vec4(w, 1.0); vD = -mv.z;
        gl_PointSize = clamp(uSize * (0.7 + aSeed * 0.6) / vD, 2.0, 64.0);
        gl_Position = projectionMatrix * mv; }`,
    fragmentShader: `uniform vec3 uColor; uniform float uOpacity; uniform float uFade; varying float vD;
      void main() { vec2 c = gl_PointCoord - 0.5;
        float wdt = max(0.035, 1.2 / max(gl_PointSize, 1.0));                // ~1,2 pixeli fizici, cel puțin 3,5 %
        if (abs(c.x + c.y * 0.12) > wdt) discard;                             // ușor înclinată de vânt
        float a = smoothstep(0.5, -0.1, c.y) * smoothstep(-0.5, -0.3, c.y);
        gl_FragColor = vec4(uColor, a * uOpacity * (1.0 - smoothstep(uFade * 0.5, uFade, vD))); }`,
  });
  const snowMat = new THREE.ShaderMaterial({
    uniforms: { ...precipUniforms(), uSize: { value: 0.42 * PR * H } }, transparent: true, depthWrite: false,
    vertexShader: `uniform float uTime; uniform vec3 uCenter; uniform float uSize; attribute float aSeed; varying float vD;
      ${WRAP}
      void main() {
        vec3 w = wrapPos(position, uTime * (1.1 + aSeed * 0.7));
        w.x += sin(uTime * 0.7 + aSeed * 40.0) * 0.6; w.z += cos(uTime * 0.5 + aSeed * 30.0) * 0.5;   // fulgii plutesc
        vec4 mv = viewMatrix * vec4(w, 1.0); vD = -mv.z;
        gl_PointSize = clamp(uSize * (0.6 + aSeed * 0.8) / vD, ${(1.5 * PR).toFixed(2)}, 26.0);
        gl_Position = projectionMatrix * mv; }`,
    fragmentShader: `uniform vec3 uColor; uniform float uOpacity; uniform float uFade; varying float vD;
      void main() { float r = length(gl_PointCoord - 0.5); if (r > 0.5) discard;
        gl_FragColor = vec4(uColor, uOpacity * smoothstep(0.5, 0.2, r) * (1.0 - smoothstep(uFade * 0.5, uFade, vD))); }`,
  });
  const RAIN_MAX = 9000, SNOW_MAX = 6000;
  const rain = new THREE.Points(precipGeo(RAIN_MAX, false), rainMat);
  const snow = new THREE.Points(precipGeo(SNOW_MAX, false), snowMat);
  for (const o of [rain, snow]) { o.frustumCulled = false; o.visible = false; o.renderOrder = 3; scene.add(o); }
  // fulgerele (la furtună): un trăsnet în zare și lumina care sare pe toată scena câteva zecimi de secundă
  const boltMat = new THREE.MeshBasicMaterial({ color: new THREE.Color(0xdfeaff).multiplyScalar(3), fog: false });
  const bolt = new THREE.Mesh(new THREE.BufferGeometry(), boltMat); bolt.visible = false; scene.add(bolt);
  let storm = 0, flashAt = 0, flashT = -1;
  const strike = (t) => {
    const th = Math.random() * Math.PI * 2, r = 70 + Math.random() * 60, pts = [];
    let x = Math.cos(th) * r, z = Math.sin(th) * r;
    for (let y = 48; y > 0; y -= 4 + Math.random() * 4) { pts.push(new THREE.Vector3(x, y, z)); x += (Math.random() - 0.5) * 6; z += (Math.random() - 0.5) * 6; }
    pts.push(new THREE.Vector3(x, 0, z));
    bolt.geometry.dispose();
    bolt.geometry = new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pts, false, "catmullrom", 0), 40, 0.35, 5);
    flashT = t;
  };

  // ---- fluxurile: cablurile merg pe pereți și pe acoperiș, ca niște trasee de cablu reale (segmente drepte,
  // colțuri rotunjite, la ~10 cm de suprafețe), nu prin ziduri; pe ele, particulele luminoase.
  // Garajul: pereți x 4,4…8,8, z −2,4…3,6, acoperiș sus la y 3,345 (margini x 8,95, z 3,75); invertorul pe
  // peretele x = 8,8 (z −0,7…0,1, y 1,23…2,28); bateria lipită de același perete (z 1,22…2,17, sus la ~1,9).
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
  const IZ = INV.z, WX = 8.92, PZ = 0.35;                                   // fața invertorului / cablul pe peretele garajului
  const FLOW = {
    // de la panouri: peste atic, în jos pe lângă perete până pe acoperișul garajului, pe acoperiș până la margine,
    // apoi în jos pe peretele garajului, în capul invertorului
    // (pe la z 0,35: între cele două ferestre laterale ale etajului, z −2,4…0 și 0,8…3,2)
    pv: conduit([[3.6, 6.55, PZ], [4.95, 6.55, PZ], [4.95, 6.98, PZ], [5.45, 6.98, PZ], [5.45, 3.43, PZ], [WX + 0.12, 3.43, PZ],
                 [WX + 0.12, 2.75, PZ], [WX, 2.75, PZ], [WX, 2.75, IZ], [WX, INV.y + 0.53, IZ]]),
    // spre baterie: din partea de jos a invertorului, pe perete, în lateralul turnului de module
    bat: conduit([[WX, INV.y - 0.53, IZ - 0.25], [WX, 0.92, IZ - 0.25], [WX, 0.92, BAT.z - 0.5]], 0.2),
    // spre casă: în sus pe perete, pe sub streașină până la colțul din față, pe fața garajului (deasupra ușii),
    // apoi în peretele casei, deasupra intrării
    load: conduit([[WX, INV.y - 0.53, IZ + 0.25], [WX, 1.1, IZ + 0.25], [WX, 1.1, IZ + 1.0], [WX, 2.8, IZ + 1.0], [WX, 2.8, GZ + 3.12], [GX - 2.2, 2.8, GZ + 3.12],
                   [HX + 4.35, 2.8, GZ + 3.12], [HX + 4.35, 2.8, HZ + 3.56]]),
    grid: new THREE.CatmullRomCurve3(wirePts),               // cablul aerian, cu săgeată
  };
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

  // ---- zi / noapte și vremea: `cloud` 0–1 (acoperirea cerului) și `wx` = {kind: clear|rain|snow|fog|storm,
  // intensity 0–1, snow_cm: zăpada depusă} din prognoza meteo
  const mix = (a, b, f) => new THREE.Color(a).lerp(new THREE.Color(b), f);
  const SKY = {
    day: [["#2d6f9e", "#7fb4d6", "#cfe2ec"], ["#7b8893", "#a2adb5", "#c9cfd3"], ["#3e464d", "#5b646b", "#7e878d"]],
    night: [["#071019", "#0d2030", "#14283a"], ["#0b0f13", "#141b21", "#1b232a"], ["#050709", "#0a0e11", "#0f1418"]],
  };
  // zăpada depusă: cât se albește fiecare material (drumul și trotuarul mai puțin: sunt curățate / se topește)
  const SNOWY = [[mat.grass, 1], [mat.field, 1], [mat.forest, 1], [mat.roof, 0.95], [mat.tile, 0.9], [mat.garage, 0.3],
                 [mat.conifer, 0.45], [mat.leaf, 0.35], [mat.leaf2, 0.35], [mat.stone, 0.7], [mat.walk, 0.6], [mat.asphalt, 0.3],
                 [mat.soil, 0.9], [mat.car, 0.25]];
  const WET = [[mat.asphalt, 0.62], [mat.walk, 0.78], [mat.stone, 0.8], [mat.field, 0.88], [mat.grass, 0.88], [mat.roof, 0.8]];
  let night = null, cloud = -1, fogOver = 0, wxKey = "", fogDense = 0, hemiBase = 0.8;
  let wx = { kind: "clear", intensity: 0, snow_cm: 0 };
  const applySky = (n, c, w = wx) => {
    const key = `${n}|${c.toFixed(2)}|${w.kind}|${(+w.intensity || 0).toFixed(2)}|${(+w.snow_cm || 0).toFixed(1)}`;
    if (key === wxKey) return;
    const dayChanged = night !== n;
    wxKey = key; night = n; cloud = c; wx = w;
    const wet = w.kind === "rain" || w.kind === "storm";
    const falling = wet || w.kind === "snow";
    // ploaia / ninsoarea / furtuna = cer acoperit de tot, mai închis cu cât e mai puternică
    const over = falling ? 1 : Math.min(1, Math.max(0, (c - 0.2) / 0.75));   // sub 20 % = senin, peste 95 % = acoperit
    const dark = w.kind === "storm" ? 0.75 : wet ? 0.25 + 0.45 * w.intensity : w.kind === "snow" ? 0.1 : 0;
    const [clear, grey, black] = SKY[n ? "night" : "day"];
    const g = sky.getContext("2d"), gr = g.createLinearGradient(0, 0, 0, 256);
    [0, 0.55, 1].forEach((p, i) => gr.addColorStop(p, "#" + mix(clear[i], grey[i], over).lerp(new THREE.Color(black[i]), dark).getHexString()));
    g.fillStyle = gr; g.fillRect(0, 0, 4, 256); skyTex.needsUpdate = true;
    fogDense = w.kind === "fog" ? 1 : falling ? 0.2 + 0.3 * w.intensity : 0;
    scene.fog.color.copy(n ? mix(0x0d2030, 0x161d23, over) : mix(0xbcd6e4, w.kind === "fog" ? 0xc9cdd0 : 0xb4bcc1, over))
      .lerp(new THREE.Color(n ? 0x0b0e11 : 0x8a9298), dark * 0.6);
    if (dayChanged) {
      mat.glass.emissiveIntensity = n ? 1.15 : 0;
      hemi.color.set(n ? 0x4a6a9a : 0xdfeefe); hemi.groundColor.set(n ? 0x0b1410 : 0x4d6b3a);
      sun.color.set(n ? 0x9fb8ff : 0xfff1d6);
      bloom.strength = n ? 0.5 : 0.7; bloom.threshold = n ? 2.2 : 1.6;
      nightObjs.forEach((o) => (o.visible = n));
      streetLamps.forEach((l) => (l.material = n ? lampOn : lampOff));
    }
    renderer.toneMappingExposure = (n ? 0.9 : 0.92) * (1 - 0.25 * dark);
    // culorile: zi/noapte, apoi ude (mai închise, drumul lucios) sau albite de zăpadă
    for (const [m, col] of Object.values(n ? NIGHT : DAY)) m.color.set(col);
    const snowAmt = Math.min(1, (+w.snow_cm || 0) / 6);
    for (const [m, f] of WET) m.color.multiplyScalar(wet && !snowAmt ? 1 - (1 - f) * (0.5 + 0.5 * w.intensity) : 1);
    mat.asphalt.roughness = wet && !snowAmt ? 0.35 : 0.95; mat.walk.roughness = wet && !snowAmt ? 0.5 : 0.95;
    for (const [m, f] of SNOWY) m.color.lerp(new THREE.Color(n ? 0x7a8592 : 0xf2f5f8), snowAmt * f);
    // cu cerul acoperit: soarele aproape dispare (umbre slabe), lumina vine difuz de peste tot
    sun.intensity = (n ? 0.35 * (1 - 0.7 * over) : 2.3 * (1 - 0.72 * over)) * (1 - 0.5 * dark);
    sun.shadow.radius = 4 + 10 * over;
    hemiBase = hemi.intensity = (n ? 0.35 : 0.8 + 0.35 * over) * (1 - 0.35 * dark);
    scene.environmentIntensity = (n ? 0.15 : 0.55 - 0.1 * over) * (1 - 0.4 * dark);
    stars.visible = n && c < 0.6 && !falling;
    const nShadow = n || falling ? 0 : Math.round(c * shadows.length);
    shadows.forEach((sh, i) => (sh.visible = i < nShadow));
    shadowAlpha = n ? 0 : 0.2 * (1 - Math.min(1, Math.max(0, (over - 0.6) / 0.35)));   // acoperit de tot: fără umbre
    const cov = falling ? 1 : c;
    for (const grp of Object.values(clouds)) {
      const shown = cov < 0.1 ? 0 : Math.round(Math.max(1, cov * grp.length));
      grp.forEach((cl, i) => (cl.visible = i < shown));
    }
    cloudMat.color.copy(n ? mix(0x4a5660, 0x343d45, over) : mix(0xffffff, 0x9ea6ad, over)).lerp(new THREE.Color(n ? 0x1c2227 : 0x5c646b), dark);
    cloudMat.opacity = 0.9 + 0.08 * over;
    allClouds.forEach((cl) => cl.material.color.copy(cloudMat.color));
    fogOver = over;
    // ploaia / ninsoarea: câte particule se desenează și cât de vizibile sunt
    rain.visible = wet; snow.visible = w.kind === "snow";
    rain.geometry.setDrawRange(0, Math.round(RAIN_MAX * (0.15 + 0.85 * w.intensity)));
    snow.geometry.setDrawRange(0, Math.round(SNOW_MAX * (0.2 + 0.8 * w.intensity)));
    rainMat.uniforms.uColor.value.set(n ? 0x6f7f8f : 0xc8d6e4); rainMat.uniforms.uOpacity.value = n ? 0.45 : 0.6;
    snowMat.uniforms.uColor.value.set(n ? 0xaab4c0 : 0xffffff); snowMat.uniforms.uOpacity.value = n ? 0.7 : 0.95;
    storm = w.kind === "storm" ? Math.max(0.6, +w.intensity || 0) : 0;
  };
  applySky(false, 0);

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
    camera.aspect = W / H; camera.updateProjectionMatrix();
    if (!moved) placeCamera();                                    // vederea aleasă de utilizator rămâne
  };
  const ro = new ResizeObserver(resize); ro.observe(host);

  let raf = 0, alive = true;
  const clock = new THREE.Clock();
  const tmpV = new THREE.Vector3(), tmpV2 = new THREE.Vector3();
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
    const dt = Math.min(0.1, t - (frame.last ?? t)); frame.last = t;
    // ceața începe dincolo de casă, oricât de departe e camera (altfel, depărtată, imaginea se spăla); la ceață
    // adevărată / ploaie deasă se apropie
    const dist = camera.position.distanceTo(controls.target);
    const fogNear = dist + 10 - 18 * fogOver, fogFar = dist + 80 - 25 * fogOver;
    scene.fog.near = wx.kind === "fog" ? Math.max(4, dist - 22) : fogNear - 14 * fogDense;
    scene.fog.far = wx.kind === "fog" ? dist + 30 : fogFar - 40 * fogDense;
    // norii: un nor care ajunge pe ecran în dreptul casei sau prea aproape de cameră se estompează
    const home = tmpV.copy(controls.target).project(camera);
    for (const cl of allClouds) if (cl.visible) {
      moveCloud(cl, t, dt);
      const p = tmpV2.copy(cl.position).project(camera);
      const r = Math.hypot(p.x - home.x, (p.y - home.y) * 0.8);
      const near = Math.min(1, Math.max(0, (cl.position.distanceTo(camera.position) - 14) / 16));
      const f = (p.z > 1 ? 1 : Math.min(1, Math.max(0, (r - 0.45) / 0.3))) * near;
      cl.material.opacity = cloudMat.opacity * f;
      cl.material.depthWrite = f > 0.99;
    }
    for (const m of [rainMat, snowMat]) { m.uniforms.uTime.value = t; m.uniforms.uCenter.value.copy(controls.target); m.uniforms.uFade.value = dist + 60; }
    // fulgerele: din când în când, la furtună; o licărire dublă de ~0,3 s
    if (storm && t > flashAt) { strike(t); flashAt = t + 3 + Math.random() * 9 * (1.6 - storm); }
    const ft = t - flashT, flash = flashT >= 0 && ft < 0.35 ? (ft < 0.08 || (ft > 0.16 && ft < 0.26) ? 1 : 0.35) : 0;
    bolt.visible = flash > 0.5;
    hemi.intensity = hemiBase + flash * 2.6;
    for (const sh of shadows) if (sh.visible) {                   // trec încet spre dreapta; la margini se estompează
      sh.position.x += sh.userData.speed * dt;
      if (sh.position.x > 45) sh.position.x = -45;
      sh.material.opacity = shadowAlpha * (1 - Math.pow(Math.abs(sh.position.x) / 45, 4));
    }
    controls.update();
    composer.render();
    onFrame(anchors());
  };
  frame();

  return {
    // night: true/false; flows: {pv, bat, load, grid} = 1 (spre invertor/consumator), -1 (invers), 0 (oprit); soc 0–100;
    // cloud: acoperirea cerului 0–100 % (prognoza meteo; null = necunoscută → senin)
    // weather: {kind: clear|rain|snow|fog|storm, intensity 0–1, snow_cm} (prognoza meteo, `/api/sky`)
    set({ night: n, flows: fl = {}, soc, cloud: c, weather: w } = {}) {
      if (n !== undefined || c !== undefined || w !== undefined)
        applySky(n !== undefined ? !!n : !!night, c != null ? c / 100 : Math.max(0, cloud), w || wx);
      for (const [k, d] of Object.entries(fl)) if (flows[k]) {
        flows[k].dir = d; flows[k].tubeMat.opacity = d ? 0.45 : 0.12;
      }
      if (soc != null) batLeds.forEach((m, i) => m.color.set(soc > i * 25 + 5 ? 0x3ddc84 : 0x56656f));
    },
    anchors,
    resetView() { moved = false; placeCamera(); onMove(false); },   // vederea de la început
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
