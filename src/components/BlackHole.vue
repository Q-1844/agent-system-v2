<template>
  <div class="blackhole" ref="host">
    <canvas ref="cv" class="bh-canvas" :class="{ hide: fallback }"></canvas>
    <div v-if="fallback" class="bh-fallback" aria-hidden="true"></div>
    <template v-if="fallback">
      <div class="bh-grain" aria-hidden="true"></div>
      <div class="bh-vignette" aria-hidden="true"></div>
    </template>
    <!-- 引力透镜光标：靠近黑洞被拖拽拉长，视界处被吞噬 -->
    <div v-if="!fallback" ref="cursorEl" class="bh-cursor" aria-hidden="true">
      <div class="bh-cursor-ring"></div>
      <div class="bh-cursor-dot"></div>
    </div>
    <div v-if="!fallback" ref="ghostEl" class="bh-cursor bh-ghost" aria-hidden="true">
      <div class="bh-cursor-ring"></div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue';

/**
 * Gargantua 级黑洞：3D 测地线光线弯曲（史瓦西场辛欧拉积分）。
 * 视界边缘、盘上方环绕弧、近侧前方穿越、光子环均为光线积分的物理涌现，
 * 非屏幕空间近似。后处理：亮部提取 + 可分离高斯 bloom + ACES 色调映射 + 颗粒。
 * 管线参考 James et al. (2015) 与 Interstellar VFX 的实时简化实现（MIT）。
 */
const host = ref<HTMLElement | null>(null);
const cv = ref<HTMLCanvasElement | null>(null);
const cursorEl = ref<HTMLElement | null>(null);
const ghostEl = ref<HTMLElement | null>(null);
const fallback = ref(false);
// 光标像素位置（host 坐标系）
const ptr = { x: 0, y: 0, inside: false };

const VERT = `#version 300 es
const vec2 verts[3] = vec2[3](vec2(-1.0, -1.0), vec2(3.0, -1.0), vec2(-1.0, 3.0));
void main() { gl_Position = vec4(verts[gl_VertexID], 0.0, 1.0); }`;

// ============ Pass 1：测地线场景 ============
const FRAG_SCENE = `#version 300 es
precision highp float;
out vec4 fragColor;

uniform vec2  u_res;
uniform float u_time;
uniform vec2  u_mouse;

const float RS       = 1.0;    // 史瓦西半径（场景单位）
const float DISK_IN  = 1.65;   // 盘内缘（≈ISCO）
const float DISK_OUT = 7.5;
const int   STEPS    = 190;

float hash21(vec2 p) {
  p = fract(p * vec2(123.34, 456.21));
  p += dot(p, p + 45.32);
  return fract(p.x * p.y);
}

float noise2(vec2 p) {
  vec2 i = floor(p), f = fract(p);
  vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(mix(hash21(i), hash21(i + vec2(1, 0)), u.x),
             mix(hash21(i + vec2(0, 1)), hash21(i + vec2(1, 1)), u.x), u.y);
}

float fbm(vec2 p) {
  float v = 0.0, a = 0.5;
  for (int i = 0; i < 4; i++) { v += a * noise2(p); p = p * 2.11 + vec2(17.3, 9.1); a *= 0.5; }
  return v;
}

// 方向空间星场（最终光线方向采样 → 透镜弯曲自然涌现）
float starField(vec3 d) {
  vec2 suv = vec2(atan(d.z, d.x), acos(clamp(d.y, -1.0, 1.0)));
  float s = 0.0;
  for (int L = 0; L < 2; L++) {
    float sc = L == 0 ? 30.0 : 55.0;
    vec2 q = suv * sc;
    vec2 g = floor(q), f = fract(q);
    float h = hash21(g + float(L) * 71.7);
    if (h > (L == 0 ? 0.962 : 0.978)) {
      vec2 pos = vec2(hash21(g + 11.3), hash21(g + 27.7));
      float dd = length(f - pos);
      s += smoothstep(0.10, 0.0, dd) * (L == 0 ? 0.9 : 0.5)
         * (0.6 + 0.4 * sin(u_time * 1.2 + h * 40.0));
    }
  }
  // 银河带：倾斜大圆
  float bandD = abs(dot(d, normalize(vec3(0.42, 0.85, 0.32))));
  s += exp(-bandD * bandD * 14.0) * fbm(suv * 3.0 + 7.0) * 0.16;
  return s;
}

// 盘面丝状纹理：对数半径 × 角向拉伸 + 域扭曲，角坐标随开普勒速度进动
float diskStreaks(float r, float ang) {
  float rot = u_time * 0.9 * pow(r, -1.5);       // Ω ∝ r^-3/2 差速剪切
  float a = ang + rot;
  vec2 q = vec2(log(r) * 5.0, a * 1.6);
  float n = fbm(q);
  n = fbm(q * 2.2 + n * 1.8 + vec2(0.0, a * 0.4));
  return n;
}

void main() {
  float minDim = min(u_res.x, u_res.y);
  // 构图偏移：黑洞位于面板纵向约 44%
  vec2 uv = (gl_FragCoord.xy - 0.5 * u_res) / minDim;
  uv.y -= 0.055 * u_res.y / minDim;

  // ---- 相机（近掠视角，Gargantua 式 84° 倾角） ----
  float az = u_mouse.x * 0.10 + sin(u_time * 0.07) * 0.015;
  float camH = 1.05 + u_mouse.y * 0.35;
  vec3 camPos = vec3(sin(az) * 9.5, camH, -cos(az) * 9.5);
  vec3 fwd = normalize(-camPos);
  vec3 right = normalize(cross(fwd, vec3(0.0, 1.0, 0.0)));
  vec3 up = cross(right, fwd);
  vec3 rd = normalize((uv.x * right + uv.y * up) * 1.15 + fwd);

  // 远距离光线直接出画（冲量参数大 → 弯曲可忽略）
  float impactB = length(cross(camPos, rd));
  if (impactB > 8.5) {
    fragColor = vec4(vec3(starField(rd) * 0.9), 1.0);
    return;
  }

  // ---- 测地线积分（辛欧拉：v += a·dt; p += v̂·dt） ----
  vec3 p = camPos;
  vec3 v = rd;
  vec3 col = vec3(0.0);
  float accum = 0.0;          // 盘不透明度累积（多次穿越：正像/环绕弧/底弧）
  bool captured = false;

  for (int i = 0; i < STEPS; i++) {
    float r = length(p);
    if (r < RS) { captured = true; break; }
    if (r > 22.0) break;

    vec3 h = cross(p, v);
    float h2 = dot(h, h);
    float dt = clamp(0.05 * r, 0.03, 0.32);
    v += (-1.5 * h2 * p / pow(r, 5.0)) * dt;   // 史瓦西光子加速度
    v = normalize(v);

    vec3 pPrev = p;
    p += v * dt;

    // 盘平面（y=0）穿越
    if (pPrev.y * p.y < 0.0) {
      float t = pPrev.y / (pPrev.y - p.y);
      vec3 hit = mix(pPrev, p, t);
      float hr = length(hit.xz);
      if (hr > DISK_IN && hr < DISK_OUT) {
        float ang = atan(hit.z, hit.x);
        float streaks = diskStreaks(hr, ang);
        // 径向轮廓：内缘炽热 → 外缘余烬
        float prof = smoothstep(DISK_IN, DISK_IN + 0.55, hr)
                   * (1.0 - smoothstep(DISK_OUT * 0.45, DISK_OUT, hr))
                   * pow(DISK_IN / hr, 1.15);
        // 相对论多普勒：朝向观察者一侧增亮
        vec3 vel = normalize(vec3(-hit.z, 0.0, hit.x));
        float beam = dot(vel, -v);
        float dop = min(pow(1.0 + 0.38 * beam, 3.0), 3.4);
        // 引力红移（内缘衰减）
        float redshift = sqrt(max(1.0 - RS / hr, 0.15));

        float bright = prof * (0.35 + 1.5 * streaks) * dop * redshift * 2.6;
        float alpha = clamp(prof * 1.4, 0.0, 0.85);
        col += (1.0 - accum) * alpha * vec3(bright);
        accum += (1.0 - accum) * alpha;
      }
    }
  }

  if (!captured) {
    col += (1.0 - accum) * vec3(starField(v) * 0.9);
  }

  fragColor = vec4(col, 1.0);
}`;

// ============ Pass 2：亮部提取 + 水平模糊 ============
const FRAG_BLUR_H = `#version 300 es
precision highp float;
out vec4 fragColor;
uniform sampler2D u_tex;
uniform vec2 u_texel;
void main() {
  vec2 uv = gl_FragCoord.xy * u_texel;
  vec3 c = max(texture(u_tex, uv).rgb - vec3(0.72), vec3(0.0)) * 0.227027;
  c += max(texture(u_tex, uv + vec2(1.3846153, 0.0) * u_texel).rgb - vec3(0.72), vec3(0.0)) * 0.3162162;
  c += max(texture(u_tex, uv - vec2(1.3846153, 0.0) * u_texel).rgb - vec3(0.72), vec3(0.0)) * 0.3162162;
  c += max(texture(u_tex, uv + vec2(3.2307692, 0.0) * u_texel).rgb - vec3(0.72), vec3(0.0)) * 0.0702703;
  c += max(texture(u_tex, uv - vec2(3.2307692, 0.0) * u_texel).rgb - vec3(0.72), vec3(0.0)) * 0.0702703;
  fragColor = vec4(c, 1.0);
}`;

// ============ Pass 3：垂直模糊 ============
const FRAG_BLUR_V = `#version 300 es
precision highp float;
out vec4 fragColor;
uniform sampler2D u_tex;
uniform vec2 u_texel;
void main() {
  vec2 uv = gl_FragCoord.xy * u_texel;
  vec3 c = texture(u_tex, uv).rgb * 0.227027;
  c += texture(u_tex, uv + vec2(0.0, 1.3846153) * u_texel).rgb * 0.3162162;
  c += texture(u_tex, uv - vec2(0.0, 1.3846153) * u_texel).rgb * 0.3162162;
  c += texture(u_tex, uv + vec2(0.0, 3.2307692) * u_texel).rgb * 0.0702703;
  c += texture(u_tex, uv - vec2(0.0, 3.2307692) * u_texel).rgb * 0.0702703;
  fragColor = vec4(c, 1.0);
}`;

// ============ Pass 4：合成（bloom + ACES + 颗粒 + 暗角） ============
const FRAG_COMPOSITE = `#version 300 es
precision highp float;
out vec4 fragColor;
uniform sampler2D u_scene;
uniform sampler2D u_bloom;
uniform vec2 u_res;
uniform float u_time;

float hash21(vec2 p) {
  p = fract(p * vec2(123.34, 456.21));
  p += dot(p, p + 45.32);
  return fract(p.x * p.y);
}

vec3 aces(vec3 x) {
  return clamp((x * (2.51 * x + 0.03)) / (x * (2.43 * x + 0.59) + 0.14), 0.0, 1.0);
}

void main() {
  vec2 uv = gl_FragCoord.xy / u_res;
  vec3 col = texture(u_scene, uv).rgb + texture(u_bloom, uv).rgb * 0.85;
  col = aces(col * 1.05);
  col += (hash21(gl_FragCoord.xy + fract(u_time) * 91.7) - 0.5) * 0.024;
  vec2 c = uv - 0.5;
  col *= 1.0 - dot(c, c) * 0.55;
  fragColor = vec4(max(col, vec3(0.0)), 1.0);
}`;

interface Target { fbo: WebGLFramebuffer; tex: WebGLTexture; w: number; h: number; }

let gl: WebGL2RenderingContext | null = null;
let progScene: WebGLProgram | null = null;
let progBlurH: WebGLProgram | null = null;
let progBlurV: WebGLProgram | null = null;
let progComp: WebGLProgram | null = null;
let sceneRT: Target | null = null;
let bloomA: Target | null = null;
let bloomB: Target | null = null;
let texFloatOK = false;

let raf = 0;
let ro: ResizeObserver | null = null;
let io: IntersectionObserver | null = null;
let inView = true;
let scale = 0.8;
let frames = 0;
let slowFrames = 0;
let adapted = false;
const mouse = { x: 0, y: 0, tx: 0, ty: 0 };

const reduced = typeof window !== 'undefined'
  && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

function compile(type: number, src: string): WebGLShader | null {
  if (!gl) return null;
  const sh = gl.createShader(type);
  if (!sh) return null;
  gl.shaderSource(sh, src);
  gl.compileShader(sh);
  if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
    console.warn('[BlackHole] shader compile failed:', gl.getShaderInfoLog(sh));
    gl.deleteShader(sh);
    return null;
  }
  return sh;
}

function link(fsSrc: string): WebGLProgram | null {
  if (!gl) return null;
  const vs = compile(gl.VERTEX_SHADER, VERT);
  const fs = compile(gl.FRAGMENT_SHADER, fsSrc);
  if (!vs || !fs) return null;
  const prog = gl.createProgram();
  gl.attachShader(prog, vs);
  gl.attachShader(prog, fs);
  gl.linkProgram(prog);
  if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
    console.warn('[BlackHole] link failed:', gl.getProgramInfoLog(prog));
    return null;
  }
  return prog;
}

function createTarget(w: number, h: number): Target | null {
  if (!gl) return null;
  const tex = gl.createTexture()!;
  gl.bindTexture(gl.TEXTURE_2D, tex);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  if (texFloatOK) {
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA16F, w, h, 0, gl.RGBA, gl.HALF_FLOAT, null);
  } else {
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, w, h, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
  }
  const fbo = gl.createFramebuffer()!;
  gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
  gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, tex, 0);
  const ok = gl.checkFramebufferStatus(gl.FRAMEBUFFER) === gl.FRAMEBUFFER_COMPLETE;
  gl.bindFramebuffer(gl.FRAMEBUFFER, null);
  if (!ok) { gl.deleteTexture(tex); gl.deleteFramebuffer(fbo); return null; }
  return { fbo, tex, w, h };
}

function destroyTarget(t: Target | null) {
  if (!gl || !t) return;
  gl.deleteTexture(t.tex);
  gl.deleteFramebuffer(t.fbo);
}

function buildTargets(w: number, h: number) {
  destroyTarget(sceneRT); destroyTarget(bloomA); destroyTarget(bloomB);
  sceneRT = createTarget(w, h);
  bloomA = createTarget(w >> 1, h >> 1);
  bloomB = createTarget(w >> 1, h >> 1);
}

function setSize() {
  if (!gl || !cv.value || !host.value) return;
  const w = host.value.clientWidth;
  const h = host.value.clientHeight;
  if (!w || !h) return;
  const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
  const pw = Math.max(2, Math.floor(w * dpr * scale));
  const ph = Math.max(2, Math.floor(h * dpr * scale));
  if (cv.value.width !== pw || cv.value.height !== ph) {
    cv.value.width = pw;
    cv.value.height = ph;
    buildTargets(pw, ph);
  }
}

function draw(t: number) {
  if (!gl || !progScene || !progBlurH || !progBlurV || !progComp || !sceneRT || !bloomA || !bloomB || !cv.value) return;

  // Pass 1：测地线场景 → sceneRT
  gl.bindFramebuffer(gl.FRAMEBUFFER, sceneRT.fbo);
  gl.viewport(0, 0, sceneRT.w, sceneRT.h);
  gl.useProgram(progScene);
  gl.uniform2f(gl.getUniformLocation(progScene, 'u_res'), sceneRT.w, sceneRT.h);
  gl.uniform1f(gl.getUniformLocation(progScene, 'u_time'), t);
  gl.uniform2f(gl.getUniformLocation(progScene, 'u_mouse'), mouse.x, mouse.y);
  gl.drawArrays(gl.TRIANGLES, 0, 3);

  // Pass 2：亮部 + 水平模糊 → bloomA
  gl.bindFramebuffer(gl.FRAMEBUFFER, bloomA.fbo);
  gl.viewport(0, 0, bloomA.w, bloomA.h);
  gl.useProgram(progBlurH);
  gl.activeTexture(gl.TEXTURE0);
  gl.bindTexture(gl.TEXTURE_2D, sceneRT.tex);
  gl.uniform1i(gl.getUniformLocation(progBlurH, 'u_tex'), 0);
  gl.uniform2f(gl.getUniformLocation(progBlurH, 'u_texel'), 1 / bloomA.w, 1 / bloomA.h);
  gl.drawArrays(gl.TRIANGLES, 0, 3);

  // Pass 3：垂直模糊 → bloomB
  gl.bindFramebuffer(gl.FRAMEBUFFER, bloomB.fbo);
  gl.viewport(0, 0, bloomB.w, bloomB.h);
  gl.useProgram(progBlurV);
  gl.bindTexture(gl.TEXTURE_2D, bloomA.tex);
  gl.uniform1i(gl.getUniformLocation(progBlurV, 'u_tex'), 0);
  gl.uniform2f(gl.getUniformLocation(progBlurV, 'u_texel'), 1 / bloomB.w, 1 / bloomB.h);
  gl.drawArrays(gl.TRIANGLES, 0, 3);

  // Pass 4：合成到屏幕
  gl.bindFramebuffer(gl.FRAMEBUFFER, null);
  gl.viewport(0, 0, cv.value.width, cv.value.height);
  gl.useProgram(progComp);
  gl.activeTexture(gl.TEXTURE0);
  gl.bindTexture(gl.TEXTURE_2D, sceneRT.tex);
  gl.uniform1i(gl.getUniformLocation(progComp, 'u_scene'), 0);
  gl.activeTexture(gl.TEXTURE1);
  gl.bindTexture(gl.TEXTURE_2D, bloomB.tex);
  gl.uniform1i(gl.getUniformLocation(progComp, 'u_bloom'), 1);
  gl.uniform2f(gl.getUniformLocation(progComp, 'u_res'), cv.value.width, cv.value.height);
  gl.uniform1f(gl.getUniformLocation(progComp, 'u_time'), t);
  gl.drawArrays(gl.TRIANGLES, 0, 3);
  gl.activeTexture(gl.TEXTURE0);
}

// ---- 引力透镜光标：拖拽、径向拉伸（意大利面化）、幽鬼二次像、视界吞噬 ----
function updateCursor() {
  const el = cursorEl.value;
  if (!el || !host.value) return;
  const ghost = ghostEl.value;
  const w = host.value.clientWidth;
  const h = host.value.clientHeight;
  const minDim = Math.min(w, h);
  // 与着色器构图一致：洞心位于 (50%, 44.5%)
  const cx = w * 0.5;
  const cy = h * 0.445;

  if (!ptr.inside) {
    el.style.opacity = '0';
    if (ghost) ghost.style.opacity = '0';
    return;
  }

  const dx = ptr.x - cx;
  const dy = ptr.y - cy;
  const d = Math.hypot(dx, dy) + 1e-3;
  const ux = dx / d;
  const uy = dy / d;

  // 透镜影响域：越靠近越强（~1/d²）
  const R0 = minDim * 0.5;
  const influence = Math.min(1, (R0 / Math.max(d, minDim * 0.08)) ** 2);
  // 视界捕获：进入 1.35 倍视界半径开始被吞噬
  const horizon = minDim * 0.21;
  const capture = Math.min(1, Math.max(0, (horizon * 1.35 - d) / (horizon * 0.6)));

  // 表观位置被引力拖向洞心
  const pull = influence * minDim * 0.045 + capture * minDim * 0.03;
  const px = ptr.x + ux * pull;
  const py = ptr.y + uy * pull;

  // 意大利面化：径向拉长、切向压扁
  const sRad = 1 + influence * 0.8 + capture * 1.2;
  const sTan = Math.max(0.22, 1 - influence * 0.3 - capture * 0.55);
  const ang = (Math.atan2(dy, dx) * 180) / Math.PI;

  el.style.opacity = String(Math.max(0, 1 - capture * 1.05));
  el.style.transform = `translate(${px}px, ${py}px) rotate(${ang}deg) scale(${sRad}, ${sTan})`;

  // 幽鬼二次像：沿爱因斯坦环（对侧）滑动的暗淡镜像
  if (ghost) {
    const ringR = minDim * 0.235;
    const gx = cx - ux * ringR;
    const gy = cy - uy * ringR;
    const gop = influence * (1 - capture) * 0.35;
    const gs = 0.5 + influence * 0.35;
    ghost.style.opacity = String(gop);
    ghost.style.transform = `translate(${gx}px, ${gy}px) rotate(${ang + 180}deg) scale(${gs}, ${gs * 0.65})`;
  }
}

function loop() {
  raf = requestAnimationFrame(loop);
  if (!inView || document.hidden) return;
  mouse.x += (mouse.tx - mouse.x) * 0.05;
  mouse.y += (mouse.ty - mouse.y) * 0.05;
  updateCursor();
  if (reduced) return;   // 光标反馈保留，黑洞本体静止

  const start = performance.now();
  draw(start / 1000);

  // 自适应：持续偏慢则降一档分辨率（只降一次）
  if (!adapted && frames++ > 20) {
    if (performance.now() - start > 28) slowFrames++;
    else slowFrames = 0;
    if (slowFrames > 30) {
      adapted = true;
      scale = 0.55;
      setSize();
    }
  }
}

function onPointer(e: PointerEvent) {
  if (!host.value) return;
  const rect = host.value.getBoundingClientRect();
  ptr.x = e.clientX - rect.left;
  ptr.y = e.clientY - rect.top;
  ptr.inside = ptr.x >= 0 && ptr.x <= rect.width && ptr.y >= 0 && ptr.y <= rect.height;
  const x = ptr.x / rect.width - 0.5;
  const y = 0.5 - ptr.y / rect.height;
  mouse.tx = Math.max(-1, Math.min(1, x * 2)) * 0.7;
  mouse.ty = Math.max(-1, Math.min(1, y * 2)) * 0.7;
}

onMounted(() => {
  const canvas = cv.value;
  if (!canvas) return;

  gl = canvas.getContext('webgl2', { alpha: false, antialias: false, depth: false, stencil: false });
  if (!gl) { fallback.value = true; return; }
  texFloatOK = !!gl.getExtension('EXT_color_buffer_float');

  progScene = link(FRAG_SCENE);
  progBlurH = link(FRAG_BLUR_H);
  progBlurV = link(FRAG_BLUR_V);
  progComp = link(FRAG_COMPOSITE);
  if (!progScene || !progBlurH || !progBlurV || !progComp) { fallback.value = true; return; }

  setSize();
  if (!sceneRT || !bloomA || !bloomB) { fallback.value = true; return; }
  draw(0);

  ro = new ResizeObserver(() => {
    setSize();
    if (reduced) draw(performance.now() / 1000);
  });
  if (host.value) ro.observe(host.value);

  io = new IntersectionObserver(([entry]) => { inView = entry.isIntersecting; }, { threshold: 0.01 });
  io.observe(canvas);

  window.addEventListener('pointermove', onPointer, { passive: true });
  raf = requestAnimationFrame(loop);
});

onBeforeUnmount(() => {
  cancelAnimationFrame(raf);
  ro?.disconnect();
  io?.disconnect();
  window.removeEventListener('pointermove', onPointer);
  destroyTarget(sceneRT); destroyTarget(bloomA); destroyTarget(bloomB);
  if (gl) {
    [progScene, progBlurH, progBlurV, progComp].forEach((p) => { if (p) gl!.deleteProgram(p); });
  }
  gl = null;
});
</script>

<style lang="scss" scoped>
.blackhole {
  position: absolute;
  inset: 0;
  overflow: hidden;
  background: #050505;
  cursor: none;
}

// 引力透镜光标
.bh-cursor {
  position: absolute;
  top: 0;
  left: 0;
  z-index: 5;
  pointer-events: none;
  opacity: 0;
  mix-blend-mode: difference;
  will-change: transform, opacity;
}

.bh-cursor-ring {
  position: absolute;
  left: -10px;
  top: -10px;
  width: 20px;
  height: 20px;
  border: 1.5px solid #fff;
  border-radius: 50%;
}

.bh-cursor-dot {
  position: absolute;
  left: -1.5px;
  top: -1.5px;
  width: 3px;
  height: 3px;
  background: #fff;
  border-radius: 50%;
}

.bh-ghost .bh-cursor-ring {
  left: -8px;
  top: -8px;
  width: 16px;
  height: 16px;
  border-width: 1px;
}

.bh-canvas {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  display: block;

  &.hide { display: none; }
}

// 无 WebGL2 时的纯 CSS 回退
.bh-fallback {
  position: absolute;
  inset: 0;
  background:
    radial-gradient(ellipse 46% 13% at 50% 51%, rgba(229, 229, 229, 0.34), transparent 72%),
    radial-gradient(circle at 50% 51%, rgba(229, 229, 229, 0.5) 0 12.4%, rgba(229, 229, 229, 0.06) 13.2%, transparent 15%),
    radial-gradient(circle at 50% 51%, #000 0 12%, transparent 12.4%),
    radial-gradient(circle at 50% 45%, #141414, #050505 70%);
}

.bh-grain {
  position: absolute;
  inset: 0;
  pointer-events: none;
  opacity: 0.06;
  mix-blend-mode: overlay;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='120' height='120'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='3'/%3E%3C/filter%3E%3Crect width='120' height='120' filter='url(%23n)'/%3E%3C/svg%3E");
}

.bh-vignette {
  position: absolute;
  inset: 0;
  pointer-events: none;
  background: radial-gradient(ellipse 75% 70% at 50% 50%, transparent 45%, rgba(0, 0, 0, 0.55) 100%);
}
</style>
