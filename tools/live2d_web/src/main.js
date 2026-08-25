import { Config, CubismSetting, Live2DSprite } from "easy-live2d";
import {
  Application,
  Assets,
  Container,
  Graphics,
  Sprite,
  Ticker,
} from "pixi.js";

Config.MouseFollow = false;
Config.MotionSound = false;

const canvas = document.querySelector("#live2d-canvas");
const status = document.querySelector("#status");
const params = new URLSearchParams(window.location.search);
const modelUrl = params.get("model");
const blinkUrl = params.get("blink");
const layerManifestUrl = params.get("layers");
const accessoryManifestUrl = params.get("accessories");
const persistentParametersUrl = params.get("persistent_parameters");
const requestedLoadTimeoutMs = Number(params.get("load_timeout_ms"));
// 4K/3K Cubism 纹理首次解码会明显慢于 2K；允许宿主按模型设置冷启动窗口。
const MODEL_READY_TIMEOUT_MS =
  Number.isFinite(requestedLoadTimeoutMs) && requestedLoadTimeoutMs > 0
    ? Math.min(120000, Math.max(5000, requestedLoadTimeoutMs))
    : 30000;
const DEFAULT_ANIMATION_PROFILE = {
  enabled: true,
  intensity: 1,
  display_scale: 1,
  display_offset_y_ratio: 0,
  blink: true,
  eye_motion: true,
  head_motion: true,
  body_motion: true,
  hair_motion: true,
  root_motion: true,
  blink_parameter_mode: "smooth",
  blink_interval_ms: [2600, 4600],
  blink_duration_ms: [105, 60, 160],
};
const PARAMETER_IDS = [
  "ParamAngleX",
  "ParamAngleY",
  "ParamAngleZ",
  "ParamEyeLOpen",
  "ParamEyeROpen",
  "ParamEyeBallX",
  "ParamEyeBallY",
  "ParamBodyAngleX",
  "ParamBodyAngleY",
  "ParamBodyAngleZ",
  "ParamBreath",
  "ParamHairFront",
  "ParamHairSide",
  "ParamHairBack",
];
const LIVELY_GAZE_TARGETS = [
  {
    durationMs: 1600,
    eyeTransitionMs: 120,
    headTransitionMs: 720,
    eyeX: 0.06,
    eyeY: 0.03,
    headX: 0.04,
    headY: 0.02,
    headZ: 0,
  },
  {
    durationMs: 2050,
    eyeTransitionMs: 135,
    headTransitionMs: 920,
    eyeX: -0.72,
    eyeY: 0.62,
    headX: -0.78,
    headY: 0.12,
    headZ: -0.72,
  },
  {
    durationMs: 1250,
    eyeTransitionMs: 105,
    headTransitionMs: 680,
    eyeX: -0.04,
    eyeY: 0.06,
    headX: -0.10,
    headY: 0.03,
    headZ: -0.08,
  },
  {
    durationMs: 2350,
    eyeTransitionMs: 145,
    headTransitionMs: 1040,
    eyeX: 0.64,
    eyeY: 0.48,
    headX: 0.68,
    headY: 0.10,
    headZ: 0.56,
  },
  {
    durationMs: 1050,
    eyeTransitionMs: 95,
    headTransitionMs: 650,
    eyeX: -0.30,
    eyeY: -0.58,
    headX: -0.28,
    headY: -0.18,
    headZ: -0.20,
  },
  {
    durationMs: 2000,
    eyeTransitionMs: 125,
    headTransitionMs: 880,
    eyeX: 0.36,
    eyeY: 0.08,
    headX: 0.42,
    headY: 0.04,
    headZ: 0.30,
  },
];

function smoothStep(value) {
  const unit = Math.max(0, Math.min(1, value));
  return unit * unit * (3 - 2 * unit);
}

function sampleLivelyGaze(elapsedMs, transitionKey) {
  const totalDuration = LIVELY_GAZE_TARGETS.reduce(
    (sum, target) => sum + target.durationMs,
    0,
  );
  let remaining = ((elapsedMs % totalDuration) + totalDuration) % totalDuration;
  let index = 0;
  for (; index < LIVELY_GAZE_TARGETS.length - 1; index += 1) {
    if (remaining < LIVELY_GAZE_TARGETS[index].durationMs) {
      break;
    }
    remaining -= LIVELY_GAZE_TARGETS[index].durationMs;
  }
  const current = LIVELY_GAZE_TARGETS[index];
  const previous = LIVELY_GAZE_TARGETS[
    (index + LIVELY_GAZE_TARGETS.length - 1) % LIVELY_GAZE_TARGETS.length
  ];
  const mix = smoothStep(remaining / current[transitionKey]);
  const interpolate = (key) => previous[key] + (current[key] - previous[key]) * mix;
  return {
    index,
    x: interpolate(transitionKey === "eyeTransitionMs" ? "eyeX" : "headX"),
    y: interpolate(transitionKey === "eyeTransitionMs" ? "eyeY" : "headY"),
    z: interpolate("headZ"),
  };
}
let animationProfile = { ...DEFAULT_ANIMATION_PROFILE };
try {
  animationProfile = {
    ...animationProfile,
    ...JSON.parse(params.get("animation") || "{}"),
  };
} catch (error) {
  console.warn(`待机动画配置解析失败，使用默认值：${error}`);
}
let persistentParameters = {};
try {
  const decoded = JSON.parse(persistentParametersUrl || "{}");
  if (
    decoded &&
    typeof decoded === "object" &&
    !Array.isArray(decoded) &&
    Object.entries(decoded).every(
      ([id, value]) => typeof id === "string" && id && Number.isFinite(value),
    )
  ) {
    persistentParameters = decoded;
  } else {
    throw new Error("固定参数必须是有限数值对象");
  }
} catch (error) {
  console.warn(`固定外观参数解析失败，忽略该配置：${error}`);
}
const setStage = (stage) => {
  document.documentElement.dataset.live2dStage = stage;
};
document.documentElement.dataset.live2dReady = "false";

function fitDisplayObject(
  app,
  displayObject,
  sourceWidth,
  sourceHeight,
  scaleMultiplier = 1,
  offsetYRatio = 0,
) {
  const availableWidth = Math.max(1, app.screen.width - 12);
  const availableHeight = Math.max(1, app.screen.height - 10);
  const scale = Math.min(
    availableWidth / sourceWidth,
    availableHeight / sourceHeight,
  ) * scaleMultiplier;
  displayObject.scale.set(scale);
  displayObject.position.set(
    app.screen.width / 2,
    app.screen.height / 2 + 2 + app.screen.height * offsetYRatio,
  );
  return scale;
}

async function preflightModel(url) {
  setStage("settings-fetch");
  const settingsResponse = await fetch(url);
  if (!settingsResponse.ok) {
    throw new Error(`模型清单读取失败：HTTP ${settingsResponse.status}`);
  }
  const settings = await settingsResponse.json();
  const mocUrl = new URL(settings.FileReferences.Moc, url).toString();
  const mocResponse = await fetch(mocUrl);
  if (!mocResponse.ok) {
    throw new Error(`MOC3 读取失败：HTTP ${mocResponse.status}`);
  }
  const mocBytes = await mocResponse.arrayBuffer();
  setStage("moc-create");
  const moc = window.Live2DCubismCore.Moc.fromArrayBuffer(mocBytes);
  if (!moc) {
    const latest = window.Live2DCubismCore.Version.csmGetLatestMocVersion();
    throw new Error(`MOC3 与 Web Core 不兼容（Core latest=${latest}）`);
  }
  const coreModel = window.Live2DCubismCore.Model.fromMoc(moc);
  if (!coreModel) {
    moc._release();
    throw new Error("MOC3 无法建立 Core Model");
  }
  coreModel.release();
  moc._release();
  setStage("texture-preflight");
  for (const texture of settings.FileReferences.Textures || []) {
    // 预检只确认本地纹理存在。若用 GET 却不消费响应体，6 张 4K 纹理会
    // 占满 Chromium 同源连接池，正式 Cubism 加载因此永久排队。
    const response = await fetch(new URL(texture, url).toString(), {
      method: "HEAD",
      cache: "no-store",
    });
    if (!response.ok) {
      throw new Error(`纹理读取失败：${texture} / HTTP ${response.status}`);
    }
  }
  return settings;
}

async function loadLayeredIdle(url) {
  setStage("layered-manifest-fetch");
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`分层清单读取失败：HTTP ${response.status}`);
  }
  const manifest = await response.json();
  if (
    manifest.schema_version !== 1 ||
    !Array.isArray(manifest.canvas_size) ||
    manifest.canvas_size.length !== 2 ||
    !Array.isArray(manifest.layers)
  ) {
    throw new Error("分层清单结构无效");
  }
  const [width, height] = manifest.canvas_size;
  const blinkRegions = Array.isArray(manifest.blink_regions)
    ? manifest.blink_regions
    : [];
  for (const region of blinkRegions) {
    if (
      !Array.isArray(region) ||
      region.length !== 4 ||
      !region.every(Number.isFinite) ||
      region[0] < 0 ||
      region[1] < 0 ||
      region[0] >= region[2] ||
      region[1] >= region[3] ||
      region[2] > width ||
      region[3] > height
    ) {
      throw new Error("blink_regions 超出画布或格式无效");
    }
  }
  const root = new Container();
  root.pivot.set(width / 2, height / 2);
  const sprites = {};
  for (const layer of manifest.layers) {
    if (
      typeof layer.role !== "string" ||
      typeof layer.file !== "string" ||
      !Array.isArray(layer.pivot) ||
      layer.pivot.length !== 2
    ) {
      throw new Error("分层条目结构无效");
    }
    const texture = await Assets.load(new URL(layer.file, url).toString());
    const sprite = new Sprite(texture);
    sprite.pivot.set(layer.pivot[0], layer.pivot[1]);
    sprite.position.set(layer.pivot[0], layer.pivot[1]);
    sprite.label = layer.role;
    root.addChild(sprite);
    sprites[layer.role] = sprite;
  }
  for (const required of [
    "body",
    "arm_left",
    "arm_right",
    "head_open",
    "head_closed",
  ]) {
    if (!sprites[required]) {
      throw new Error(`分层清单缺少 ${required}`);
    }
  }
  sprites.head_closed.visible = false;
  sprites.head_closed.alpha = 1;
  // 闭眼层需要覆盖在完整睁眼头之上；透明区域仍显示下方稳定头层。
  root.addChild(sprites.head_closed);
  let blinkMask = null;
  if (blinkRegions.length > 0) {
    blinkMask = new Graphics();
    blinkMask.pivot.copyFrom(sprites.head_closed.pivot);
    blinkMask.position.copyFrom(sprites.head_closed.position);
    root.addChild(blinkMask);
    sprites.head_closed.mask = blinkMask;
  }
  return {
    root,
    sprites,
    width,
    height,
    blinkMask,
    blinkRegions,
  };
}

async function loadCubismAccessories(url) {
  setStage("accessory-manifest-fetch");
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`常驻配件清单读取失败：HTTP ${response.status}`);
  }
  const manifest = await response.json();
  if (
    ![1, 2].includes(manifest.schema_version) ||
    !Array.isArray(manifest.layers) ||
    manifest.layers.length === 0
  ) {
    throw new Error("常驻配件清单结构无效");
  }
  let binding = null;
  if (manifest.schema_version === 2) {
    const rawBinding = manifest.binding;
    if (
      manifest.render_layer !== "same_model_overlay" ||
      rawBinding?.mode !== "cubism_part_mesh_affine" ||
      typeof rawBinding?.head_part_id !== "string" ||
      !rawBinding.head_part_id.trim() ||
      typeof rawBinding?.nose_part_id !== "string" ||
      !rawBinding.nose_part_id.trim() ||
      (
        rawBinding.ear_pair_center !== undefined &&
        rawBinding.ear_pair_center !== "nose"
      )
    ) {
      throw new Error("绑定配件清单必须声明同模型 Cubism Part 网格绑定");
    }
    binding = {
      mode: rawBinding.mode,
      headPartId: rawBinding.head_part_id.trim(),
      nosePartId: rawBinding.nose_part_id.trim(),
      earPairCenter: rawBinding.ear_pair_center === "nose" ? "nose" : null,
      modelMocSha256: String(rawBinding.model_moc_sha256 || "").toLowerCase(),
      cdiFile: String(rawBinding.cdi_file || "").trim(),
      cdiSha256: String(rawBinding.cdi_sha256 || "").toLowerCase(),
      initialized: false,
      status: "pending",
      headSelection: null,
      noseSelection: null,
      referenceHeadPoints: null,
      referenceNose: null,
      currentHeadPoints: null,
      currentNose: null,
      matrix: { a: 1, b: 0, c: 0, d: 1, tx: 0, ty: 0 },
      noseResidual: { x: 0, y: 0 },
      failureCount: 0,
      lastFailure: null,
    };
  }

  // easy-live2d 直接控制共享 GL，普通 Pixi sibling 在 Qt WebEngine 中不会
  // 落入最终帧；使用已验证可见的透明宿主层，但全部姿态仍来自同一 Cubism
  // 实例的本帧 ArtMesh。root 在可见边界探测后才挂载，避免污染校准。
  const root = document.createElement("div");
  root.dataset.renderLayer = "same-model-cubism-bound-overlay";
  Object.assign(root.style, {
    position: "fixed",
    inset: "0",
    overflow: "hidden",
    pointerEvents: "none",
    zIndex: "2",
  });
  const entries = [];
  const roles = new Set();
  for (const [index, layer] of manifest.layers.entries()) {
    const role = typeof layer?.role === "string" ? layer.role.trim() : "";
    const file = typeof layer?.file === "string" ? layer.file.trim() : "";
    const positionRatio = layer?.position_ratio;
    const widthRatio = layer?.width_ratio;
    const rotationDegrees = layer?.rotation_degrees;
    if (
      !role ||
      roles.has(role) ||
      !file ||
      !Array.isArray(positionRatio) ||
      positionRatio.length !== 2 ||
      !positionRatio.every(Number.isFinite) ||
      !Number.isFinite(widthRatio) ||
      widthRatio <= 0 ||
      !Number.isFinite(rotationDegrees) ||
      (manifest.schema_version === 2 && !["head", "nose"].includes(layer.pin_to))
    ) {
      throw new Error(`常驻配件 layers[${index}] 无效`);
    }
    roles.add(role);
    const image = new Image();
    image.alt = "";
    image.decoding = "async";
    Object.assign(image.style, {
      position: "absolute",
      left: "0",
      top: "0",
      transformOrigin: "0 0",
      pointerEvents: "none",
    });
    await new Promise((resolve, reject) => {
      image.addEventListener("load", resolve, { once: true });
      image.addEventListener(
        "error",
        () => reject(new Error(`常驻配件纹理无效：${file}`)),
        { once: true },
      );
      image.src = new URL(file, url).toString();
    });
    if (
      !Number.isFinite(image.naturalWidth) ||
      image.naturalWidth <= 0 ||
      !Number.isFinite(image.naturalHeight) ||
      image.naturalHeight <= 0
    ) {
      throw new Error(`常驻配件纹理无效：${file}`);
    }
    image.dataset.role = role;
    image.style.width = `${image.naturalWidth}px`;
    image.style.height = `${image.naturalHeight}px`;
    root.appendChild(image);
    entries.push({
      role,
      image,
      pinTo: manifest.schema_version === 2 ? layer.pin_to : "head",
      positionRatio,
      widthRatio,
      rotationDegrees,
      sourceWidth: image.naturalWidth,
      sourceHeight: image.naturalHeight,
      referencePosition: null,
      referenceScale: 0,
      position: { x: 0, y: 0 },
      scale: 0,
      matrix: { a: 1, b: 0, c: 0, d: 1, tx: 0, ty: 0 },
    });
  }
  return {
    root,
    entries,
    roles: [...roles],
    schemaVersion: manifest.schema_version,
    renderLayer: manifest.schema_version === 2 ? manifest.render_layer : "legacy",
    binding,
  };
}

function cubismAccessoryBounds(model, accessories) {
  const rawBounds = model.getBounds();
  const ratios = accessories.contentBoundsRatios;
  if (!ratios) {
    return rawBounds;
  }
  return {
    x: rawBounds.x + rawBounds.width * ratios.left,
    y: rawBounds.y + rawBounds.height * ratios.top,
    width: rawBounds.width * (ratios.right - ratios.left),
    height: rawBounds.height * (ratios.bottom - ratios.top),
  };
}

function cubismIdText(value) {
  if (typeof value === "string") {
    return value;
  }
  if (typeof value?.s === "string") {
    return value.s;
  }
  const inner = value?.getString?.();
  if (typeof inner === "string") {
    return inner;
  }
  if (typeof inner?.s === "string") {
    return inner.s;
  }
  return String(value || "");
}

function cubismGeometryContext(app, model) {
  const liveModel = model?._model;
  const cubismModel = liveModel?.getModel?.();
  const coreModel = cubismModel?.getModel?.();
  const mvp = liveModel?.getRenderer?.()?.getMvpMatrix?.()?.getArray?.();
  const viewport = model?.getRenderViewport?.();
  const resolution = app?.renderer?.resolution || 1;
  if (
    !cubismModel ||
    !coreModel ||
    !mvp ||
    mvp.length !== 16 ||
    !viewport ||
    ![viewport.x, viewport.y, viewport.width, viewport.height, resolution]
      .every(Number.isFinite) ||
    viewport.width <= 0 ||
    viewport.height <= 0 ||
    resolution <= 0
  ) {
    return null;
  }
  return { cubismModel, coreModel, mvp, viewport, resolution };
}

function projectCubismVertex(context, x, y) {
  const { mvp, viewport, resolution } = context;
  const clipX = mvp[0] * x + mvp[4] * y + mvp[12];
  const clipY = mvp[1] * x + mvp[5] * y + mvp[13];
  const clipW = mvp[3] * x + mvp[7] * y + mvp[15];
  if (![clipX, clipY, clipW].every(Number.isFinite) || Math.abs(clipW) < 1e-8) {
    return null;
  }
  const ndcX = clipX / clipW;
  const ndcY = clipY / clipW;
  return {
    x: (viewport.x + (ndcX + 1) * 0.5 * viewport.width) / resolution,
    y: (viewport.y + (1 - (ndcY + 1) * 0.5) * viewport.height) /
      resolution,
  };
}

function selectCubismPartVertices(context, partId, maximumSamples = 240) {
  const { cubismModel, coreModel } = context;
  const partIds = Array.from(coreModel.parts?.ids || []);
  const partIndex = partIds.findIndex((id) => cubismIdText(id) === partId);
  if (partIndex < 0) {
    return null;
  }
  const allDrawables = [];
  const visibleDrawables = [];
  for (let index = 0; index < cubismModel.getDrawableCount(); index += 1) {
    if (cubismModel.getDrawableParentPartIndex(index) !== partIndex) {
      continue;
    }
    const count = cubismModel.getDrawableVertexCount(index);
    if (!Number.isInteger(count) || count < 3) {
      continue;
    }
    allDrawables.push({ index, count });
    if (cubismModel.getDrawableOpacity(index) > 0.01) {
      visibleDrawables.push({ index, count });
    }
  }
  // 初始中性帧锁定当前可见 ArtMesh；之后即使表情把它的 opacity 暂时
  // 降为 0，也继续跟踪同一组顶点，避免眨眼/换表情导致锚点跳变。
  const drawables = visibleDrawables.length > 0 ? visibleDrawables : allDrawables;
  if (drawables.length === 0) {
    return null;
  }
  const perDrawable = Math.max(8, Math.floor(maximumSamples / drawables.length));
  return {
    partId,
    partIndex,
    drawables: drawables.map(({ index, count }) => {
      const step = Math.max(1, Math.ceil(count / perDrawable));
      const vertexIndices = [];
      for (let vertexIndex = 0; vertexIndex < count; vertexIndex += step) {
        vertexIndices.push(vertexIndex);
      }
      if (vertexIndices.at(-1) !== count - 1) {
        vertexIndices.push(count - 1);
      }
      return { index, vertexIndices };
    }),
  };
}

function sampleCubismPartVertices(context, selection) {
  if (!selection) {
    return null;
  }
  const points = [];
  for (const drawable of selection.drawables) {
    const vertices = context.cubismModel.getDrawableVertexPositions(drawable.index);
    if (!vertices) {
      return null;
    }
    for (const vertexIndex of drawable.vertexIndices) {
      const offset = vertexIndex * 2;
      const point = projectCubismVertex(
        context,
        Number(vertices[offset]),
        Number(vertices[offset + 1]),
      );
      if (point) {
        points.push(point);
      }
    }
  }
  return points.length >= 3 ? points : null;
}

function pointBoundsCenter(points) {
  if (!Array.isArray(points) || points.length === 0) {
    return null;
  }
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const point of points) {
    minX = Math.min(minX, point.x);
    minY = Math.min(minY, point.y);
    maxX = Math.max(maxX, point.x);
    maxY = Math.max(maxY, point.y);
  }
  if (![minX, minY, maxX, maxY].every(Number.isFinite)) {
    return null;
  }
  return { x: (minX + maxX) / 2, y: (minY + maxY) / 2 };
}

function solveMeshAffine(referencePoints, currentPoints) {
  if (
    !Array.isArray(referencePoints) ||
    !Array.isArray(currentPoints) ||
    referencePoints.length !== currentPoints.length ||
    referencePoints.length < 3
  ) {
    return null;
  }
  const count = referencePoints.length;
  const sourceCenter = referencePoints.reduce(
    (sum, point) => ({ x: sum.x + point.x / count, y: sum.y + point.y / count }),
    { x: 0, y: 0 },
  );
  const targetCenter = currentPoints.reduce(
    (sum, point) => ({ x: sum.x + point.x / count, y: sum.y + point.y / count }),
    { x: 0, y: 0 },
  );
  let sxx = 0;
  let sxy = 0;
  let syy = 0;
  let ux = 0;
  let uy = 0;
  let vx = 0;
  let vy = 0;
  for (let index = 0; index < count; index += 1) {
    const sx = referencePoints[index].x - sourceCenter.x;
    const sy = referencePoints[index].y - sourceCenter.y;
    const tx = currentPoints[index].x - targetCenter.x;
    const ty = currentPoints[index].y - targetCenter.y;
    sxx += sx * sx;
    sxy += sx * sy;
    syy += sy * sy;
    ux += tx * sx;
    uy += tx * sy;
    vx += ty * sx;
    vy += ty * sy;
  }
  const sourceDeterminant = sxx * syy - sxy * sxy;
  if (!Number.isFinite(sourceDeterminant) || sourceDeterminant <= 1e-5) {
    return null;
  }
  const inverse00 = syy / sourceDeterminant;
  const inverse01 = -sxy / sourceDeterminant;
  const inverse11 = sxx / sourceDeterminant;
  const a = ux * inverse00 + uy * inverse01;
  const c = ux * inverse01 + uy * inverse11;
  const b = vx * inverse00 + vy * inverse01;
  const d = vx * inverse01 + vy * inverse11;
  const tx = targetCenter.x - a * sourceCenter.x - c * sourceCenter.y;
  const ty = targetCenter.y - b * sourceCenter.x - d * sourceCenter.y;
  const determinant = a * d - b * c;
  const trace = a * a + b * b + c * c + d * d;
  const discriminant = Math.sqrt(
    Math.max(0, trace * trace - 4 * determinant * determinant),
  );
  const maximumScale = Math.sqrt(Math.max(0, (trace + discriminant) / 2));
  const minimumScale = Math.sqrt(Math.max(0, (trace - discriminant) / 2));
  const anisotropy = maximumScale / Math.max(minimumScale, 1e-8);
  if (
    ![a, b, c, d, tx, ty, determinant, maximumScale, minimumScale, anisotropy]
      .every(Number.isFinite) ||
    determinant <= 0 ||
    minimumScale < 0.55 ||
    maximumScale > 1.65 ||
    anisotropy > 1.8
  ) {
    return null;
  }
  return { a, b, c, d, tx, ty };
}

function transformPoint(matrix, point) {
  return {
    x: matrix.a * point.x + matrix.c * point.y + matrix.tx,
    y: matrix.b * point.x + matrix.d * point.y + matrix.ty,
  };
}

function resetCubismAccessoryBinding(accessories) {
  const binding = accessories?.binding;
  if (!binding) {
    return;
  }
  binding.initialized = false;
  binding.status = "pending";
  binding.headSelection = null;
  binding.noseSelection = null;
  binding.referenceHeadPoints = null;
  binding.referenceNose = null;
  binding.currentHeadPoints = null;
  binding.currentNose = null;
  binding.matrix = { a: 1, b: 0, c: 0, d: 1, tx: 0, ty: 0 };
  binding.noseResidual = { x: 0, y: 0 };
  binding.lastFailure = null;
  for (const entry of accessories.entries) {
    entry.referencePosition = null;
    entry.referenceScale = 0;
  }
}

function setCubismAccessoryTransform(entry, bindingMatrix, noseResidual) {
  const position = transformPoint(bindingMatrix, entry.referencePosition);
  if (entry.pinTo === "nose") {
    position.x += noseResidual.x;
    position.y += noseResidual.y;
  }
  const radians = entry.rotationDegrees * Math.PI / 180;
  const cosine = Math.cos(radians) * entry.referenceScale;
  const sine = Math.sin(radians) * entry.referenceScale;
  const a = bindingMatrix.a * cosine + bindingMatrix.c * sine;
  const b = bindingMatrix.b * cosine + bindingMatrix.d * sine;
  const c = bindingMatrix.a * -sine + bindingMatrix.c * cosine;
  const d = bindingMatrix.b * -sine + bindingMatrix.d * cosine;
  const tx = position.x - a * entry.sourceWidth / 2 - c * entry.sourceHeight / 2;
  const ty = position.y - b * entry.sourceWidth / 2 - d * entry.sourceHeight / 2;
  entry.image.style.transform = `matrix(${a},${b},${c},${d},${tx},${ty})`;
  entry.position = position;
  entry.scale = Math.hypot(a, b);
  entry.matrix = { a, b, c, d, tx, ty };
}

function layoutCubismAccessories(app, model, accessories) {
  const bounds = cubismAccessoryBounds(model, accessories);
  if (
    !bounds ||
    ![bounds.x, bounds.y, bounds.width, bounds.height].every(Number.isFinite) ||
    bounds.width <= 0 ||
    bounds.height <= 0
  ) {
    return false;
  }
  const binding = accessories.binding;
  if (!binding) {
    for (const entry of accessories.entries) {
      const targetWidth = bounds.width * entry.widthRatio;
      entry.referenceScale = targetWidth / entry.sourceWidth;
      entry.referencePosition = {
        x: bounds.x + bounds.width * entry.positionRatio[0],
        y: bounds.y + bounds.height * entry.positionRatio[1],
      };
      setCubismAccessoryTransform(
        entry,
        { a: 1, b: 0, c: 0, d: 1, tx: 0, ty: 0 },
        { x: 0, y: 0 },
      );
    }
    return true;
  }

  const context = cubismGeometryContext(app, model);
  if (!context) {
    binding.status = "waiting-for-cubism-frame";
    return false;
  }
  if (!binding.initialized) {
    binding.headSelection = selectCubismPartVertices(
      context,
      binding.headPartId,
    );
    binding.noseSelection = selectCubismPartVertices(
      context,
      binding.nosePartId,
      80,
    );
    binding.referenceHeadPoints = sampleCubismPartVertices(
      context,
      binding.headSelection,
    );
    const referenceNosePoints = sampleCubismPartVertices(
      context,
      binding.noseSelection,
    );
    binding.referenceNose = pointBoundsCenter(referenceNosePoints);
    if (!binding.referenceHeadPoints || !binding.referenceNose) {
      binding.status = "missing-cubism-part-geometry";
      binding.failureCount += 1;
      binding.lastFailure = "模型缺少稳定的头部或鼻子 ArtMesh 顶点";
      return false;
    }
    for (const entry of accessories.entries) {
      const targetWidth = bounds.width * entry.widthRatio;
      entry.referenceScale = targetWidth / entry.sourceWidth;
      // pin_to=nose 的语义是让配件中心真正落在鼻部 ArtMesh 中心。
      // position_ratio 只保留为绑定缺失时的安全种子；不能再把它当作
      // 中性姿态的绝对鼻位，否则模型本身的非对称构图会留下水平偏差。
      entry.referencePosition = entry.pinTo === "nose"
        ? { ...binding.referenceNose }
        : {
            x: bounds.x + bounds.width * entry.positionRatio[0],
            y: bounds.y + bounds.height * entry.positionRatio[1],
          };
    }
    if (binding.earPairCenter === "nose") {
      const leftEar = accessories.entries.find(
        (entry) => entry.role === "pig_ear_left" && entry.pinTo === "head",
      );
      const rightEar = accessories.entries.find(
        (entry) => entry.role === "pig_ear_right" && entry.pinTo === "head",
      );
      if (!leftEar || !rightEar) {
        binding.status = "missing-paired-pig-ears";
        binding.failureCount += 1;
        binding.lastFailure = "耳朵中轴绑定要求一对 pin_to=head 的猪耳";
        return false;
      }
      // 比例只决定耳间距与高度；两耳作为整体严格以同帧鼻部 ArtMesh
      // 为中轴，避免猫耳/双马尾切换改变内容包围框后让整组看起来歪斜。
      const pairMidpointX =
        (leftEar.referencePosition.x + rightEar.referencePosition.x) / 2;
      const centerDeltaX = binding.referenceNose.x - pairMidpointX;
      leftEar.referencePosition.x += centerDeltaX;
      rightEar.referencePosition.x += centerDeltaX;
    }
    binding.initialized = true;
    binding.status = "bound";
  }

  const currentHeadPoints = sampleCubismPartVertices(
    context,
    binding.headSelection,
  );
  const currentNosePoints = sampleCubismPartVertices(
    context,
    binding.noseSelection,
  );
  const currentNose = pointBoundsCenter(currentNosePoints);
  const matrix = solveMeshAffine(binding.referenceHeadPoints, currentHeadPoints);
  if (matrix) {
    binding.matrix = matrix;
    binding.currentHeadPoints = currentHeadPoints;
    binding.currentNose = currentNose;
    binding.status = "bound";
    binding.lastFailure = null;
    if (currentNose) {
      const predictedNose = transformPoint(matrix, binding.referenceNose);
      binding.noseResidual = {
        x: currentNose.x - predictedNose.x,
        y: currentNose.y - predictedNose.y,
      };
    }
  } else {
    binding.failureCount += 1;
    binding.lastFailure = "本帧头部网格退化，沿用上一帧绑定变换";
  }
  for (const entry of accessories.entries) {
    setCubismAccessoryTransform(entry, binding.matrix, binding.noseResidual);
  }
  return true;
}

function detectCubismContentBounds(app, model) {
  const gl = app.renderer.gl;
  const canvas = app.renderer.canvas;
  const resolution = app.renderer.resolution || 1;
  if (!gl || !canvas || canvas.width <= 0 || canvas.height <= 0) {
    return null;
  }
  const pixels = new Uint8Array(canvas.width * canvas.height * 4);
  gl.readPixels(
    0,
    0,
    canvas.width,
    canvas.height,
    gl.RGBA,
    gl.UNSIGNED_BYTE,
    pixels,
  );
  let minX = canvas.width;
  let minY = canvas.height;
  let maxX = -1;
  let maxY = -1;
  for (let y = 0; y < canvas.height; y += 1) {
    for (let x = 0; x < canvas.width; x += 1) {
      if (pixels[(y * canvas.width + x) * 4 + 3] === 0) {
        continue;
      }
      minX = Math.min(minX, x);
      minY = Math.min(minY, y);
      maxX = Math.max(maxX, x);
      maxY = Math.max(maxY, y);
    }
  }
  if (maxX < minX || maxY < minY) {
    return null;
  }
  const contentBounds = {
    x: minX / resolution,
    y: (canvas.height - maxY - 1) / resolution,
    width: (maxX - minX + 1) / resolution,
    height: (maxY - minY + 1) / resolution,
  };
  const rawBounds = model.getBounds();
  if (rawBounds.width <= 0 || rawBounds.height <= 0) {
    return null;
  }
  const left = Math.max(
    0,
    Math.min(1, (contentBounds.x - rawBounds.x) / rawBounds.width),
  );
  const top = Math.max(
    0,
    Math.min(1, (contentBounds.y - rawBounds.y) / rawBounds.height),
  );
  const right = Math.max(
    0,
    Math.min(
      1,
      (contentBounds.x + contentBounds.width - rawBounds.x) /
        rawBounds.width,
    ),
  );
  const bottom = Math.max(
    0,
    Math.min(
      1,
      (contentBounds.y + contentBounds.height - rawBounds.y) /
        rawBounds.height,
    ),
  );
  if (right <= left || bottom <= top) {
    return null;
  }
  return { left, top, right, bottom };
}

function rectangleDiagnostics(bounds) {
  if (!bounds) {
    return null;
  }
  return {
    x: bounds.x,
    y: bounds.y,
    width: bounds.width,
    height: bounds.height,
  };
}

function describeCubismAccessories(model, accessories) {
  if (!accessories) {
    return {
      ready: false,
      layerCount: 0,
      roles: [],
      modelBounds: null,
      layers: [],
    };
  }
  return {
    ready: true,
    layerCount: accessories.entries.length,
    roles: accessories.roles,
    schemaVersion: accessories.schemaVersion,
    renderLayer: accessories.renderLayer,
    modelBounds: rectangleDiagnostics(
      cubismAccessoryBounds(model, accessories),
    ),
    rawModelBounds: rectangleDiagnostics(model.getBounds()),
    contentBoundsRatios: accessories.contentBoundsRatios,
    rootBounds: rectangleDiagnostics(accessories.root.getBoundingClientRect()),
    binding: accessories.binding
      ? {
          mode: accessories.binding.mode,
          status: accessories.binding.status,
          headPartId: accessories.binding.headPartId,
          nosePartId: accessories.binding.nosePartId,
          earPairCenter: accessories.binding.earPairCenter,
          initialized: accessories.binding.initialized,
          headSampleCount: accessories.binding.referenceHeadPoints?.length || 0,
          referenceNose: accessories.binding.referenceNose,
          currentNose: accessories.binding.currentNose,
          matrix: accessories.binding.matrix,
          noseResidual: accessories.binding.noseResidual,
          failureCount: accessories.binding.failureCount,
          lastFailure: accessories.binding.lastFailure,
        }
      : null,
    layers: accessories.entries.map((entry) => ({
      role: entry.role,
      pinTo: entry.pinTo,
      position: entry.position,
      scale: {
        x: entry.scale,
        y: entry.scale,
      },
      matrix: entry.matrix,
      bounds: rectangleDiagnostics(entry.image.getBoundingClientRect()),
      visible: entry.image.isConnected,
      renderable: entry.image.complete && entry.image.naturalWidth > 0,
      alpha: Number(getComputedStyle(entry.image).opacity),
    })),
  };
}

async function start() {
  setStage("start");
  if (!modelUrl) {
    throw new Error("缺少 model 参数");
  }
  if (layerManifestUrl && accessoryManifestUrl) {
    throw new Error("Cubism 常驻配件不能与分层 WebGL 后端同时启用");
  }

  const app = new Application();
  await app.init({
    canvas,
    resizeTo: window,
    backgroundAlpha: 0,
    antialias: true,
    autoDensity: true,
    // 全身小窗会把 4K 纹理压到几百像素；至少 2× 超采样可明显减轻
    // WebEngine 合成后的发糊，同时在高 DPI 屏上限制到 3× 控制显存。
    resolution: Math.min(Math.max(window.devicePixelRatio || 1, 2), 3),
  });

  const settings = await preflightModel(modelUrl);
  const modelSetting = new CubismSetting({ modelJSON: settings });
  modelSetting.redirectPath(({ file }) => new URL(file, modelUrl).toString());
  setStage("easy-live2d-create");
  const model = new Live2DSprite({
    modelSetting,
    ticker: Ticker.shared,
  });
  // 分层后端只借用 Cubism 模型完成兼容性与参数探测。底模尚未完成所有
  // ArtMesh 绑定，绝不能在分层素材加载或眨眼切换时短暂露出。
  if (layerManifestUrl) {
    model.visible = false;
    model.renderable = false;
  }
  app.stage.addChild(model);
  await Promise.race([
    model.ready,
    new Promise((_, reject) => {
      setTimeout(() => reject(new Error("easy-live2d 加载超时")), MODEL_READY_TIMEOUT_MS);
    }),
  ]);
  setStage("easy-live2d-ready");

  let layeredIdle = null;
  let displayObject = model;
  let sourceWidth = model.width;
  let sourceHeight = model.height;
  if (layerManifestUrl) {
    layeredIdle = await loadLayeredIdle(layerManifestUrl);
    app.stage.addChild(layeredIdle.root);
    model.visible = false;
    model.renderable = false;
    displayObject = layeredIdle.root;
    sourceWidth = layeredIdle.width;
    sourceHeight = layeredIdle.height;
    setStage("layered-webgl-ready");
  }
  let cubismAccessories = null;
  if (accessoryManifestUrl) {
    cubismAccessories = await loadCubismAccessories(accessoryManifestUrl);
    setStage("cubism-accessories-loaded");
  }
  document.documentElement.dataset.live2dRenderer = layeredIdle
    ? "layered-webgl"
    : "cubism-parameters";
  document.documentElement.dataset.live2dAccessories = JSON.stringify({
    ready: cubismAccessories !== null,
    layerCount: cubismAccessories?.entries.length || 0,
    roles: cubismAccessories?.roles || [],
    renderLayer: cubismAccessories?.renderLayer || null,
    bindingMode: cubismAccessories?.binding?.mode || null,
  });

  model.anchor.set(0.5);
  const parameterDefaults = {};
  const coreParameters = model?._model?.getModel?.()?.getModel?.()?.parameters;
  const coreParameterIds = Array.from(coreParameters?.ids || []);
  const coreDefaultValues = coreParameters?.defaultValues;
  const coreParameterIndices = {};
  if (coreDefaultValues && coreParameterIds.length === coreDefaultValues.length) {
    for (let index = 0; index < coreParameterIds.length; index += 1) {
      const id = cubismIdText(coreParameterIds[index]);
      const value = Number(coreDefaultValues[index]);
      if (id && Number.isFinite(value)) {
        parameterDefaults[id] = value;
        coreParameterIndices[id] = index;
      }
    }
  }
  const parameterRanges = {};
  for (const id of new Set([
    ...PARAMETER_IDS,
    ...Object.keys(persistentParameters),
  ])) {
    const range = model.getParameterValueRangeById(id);
    if (range) {
      parameterRanges[id] = range;
    }
  }
  document.documentElement.dataset.live2dParameters =
    JSON.stringify(parameterRanges);
  document.documentElement.dataset.live2dProfile =
    JSON.stringify(animationProfile);

  let animationPaused = false;
  let currentEyeOpen = 1;
  let currentBlinkClosure = 0;
  let automaticBlinkCount = 0;
  let automaticBlinkActive = false;
  let persistentApplyCount = 0;
  let gazeMoveCount = 0;
  let lastGazeTargetIndex = -1;
  let currentGaze = { index: 0, x: 0, y: 0 };
  let currentHead = { index: 0, x: 0, y: 0, z: 0 };
  const getParameterRange = (id) => {
    if (typeof id !== "string" || !id.trim()) {
      return null;
    }
    if (parameterRanges[id]) {
      return parameterRanges[id];
    }
    const range = model.getParameterValueRangeById(id);
    if (range) {
      parameterRanges[id] = range;
      document.documentElement.dataset.live2dParameters =
        JSON.stringify(parameterRanges);
    }
    return range;
  };
  const setParameter = (id, value) => {
    const range = getParameterRange(id);
    if (!range || !Number.isFinite(value)) {
      return false;
    }
    model.setParameterValueById(
      id,
      Math.max(range.min, Math.min(range.max, value)),
    );
    return true;
  };
  const setNormalizedParameter = (id, unit, fraction = 1) => {
    const range = getParameterRange(id);
    if (!range) {
      return;
    }
    const center = (range.min + range.max) / 2;
    const radius = (range.max - range.min) / 2;
    setParameter(
      id,
      center +
        radius *
          Math.max(-1, Math.min(1, unit * fraction * animationProfile.intensity)),
    );
  };
  const setUnitParameter = (id, unit) => {
    const range = getParameterRange(id);
    if (!range) {
      return;
    }
    setParameter(
      id,
      range.min +
        (range.max - range.min) * Math.max(0, Math.min(1, unit)),
    );
  };
  const defaultParameterValue = (id) => {
    const value = parameterDefaults[id];
    return Number.isFinite(value) ? value : null;
  };
  const setEyeOpenFraction = (id, fraction) => {
    const range = getParameterRange(id);
    if (!range) {
      return false;
    }
    const authoredDefault = defaultParameterValue(id);
    const openValue = authoredDefault === null ? range.max : authoredDefault;
    return setParameter(
      id,
      range.min +
        (openValue - range.min) * Math.max(0, Math.min(1, fraction)),
    );
  };
  const applyPersistentParameters = () => {
    let applied = 0;
    for (const [id, value] of Object.entries(persistentParameters)) {
      if (setParameter(id, Number(value))) {
        applied += 1;
      }
    }
    if (applied > 0) {
      persistentApplyCount += 1;
    }
    return applied;
  };
  const currentPersistentParameterValues = () => {
    const values = coreParameters?.values;
    if (!values) {
      return {};
    }
    return Object.fromEntries(
      Object.keys(persistentParameters).flatMap((id) => {
        const index = coreParameterIndices[id];
        const value = Number.isInteger(index) ? Number(values[index]) : NaN;
        return Number.isFinite(value) ? [[id, value]] : [];
      }),
    );
  };
  const neutralParameters = () => {
    for (const id of PARAMETER_IDS) {
      const authoredDefault = defaultParameterValue(id);
      if (authoredDefault === null) {
        setNormalizedParameter(id, 0);
      } else {
        setParameter(id, authoredDefault);
      }
    }
    applyPersistentParameters();
  };
  const applyLayeredBlink = (closureValue) => {
    if (!layeredIdle) {
      return false;
    }
    const closure = Math.max(0, Math.min(1, Number(closureValue) || 0));
    currentBlinkClosure = closure;
    const { sprites, blinkMask, blinkRegions } = layeredIdle;
    sprites.head_open.visible = true;
    sprites.head_open.alpha = 1;
    if (blinkMask && blinkRegions.length > 0) {
      blinkMask.clear();
      for (const [left, top, right, bottom] of blinkRegions) {
        if (closure > 0) {
          // 从上向下覆盖眼球，模拟上眼睑落下；旧椭圆会从眼睛中央向
          // 上下同时扩张，看起来像突然换成另一张表情。
          blinkMask.rect(
            left,
            top,
            right - left,
            (bottom - top) * closure,
          );
        }
      }
      if (closure > 0) {
        blinkMask.fill(0xffffff);
      }
      sprites.head_closed.visible = closure > 0.001;
      sprites.head_closed.alpha = 1;
    } else {
      // 旧宠物没有声明眼区时仍安全降级，但始终只显示一张完整头图。
      sprites.head_closed.visible = closure >= 0.5;
      sprites.head_closed.alpha = 1;
      sprites.head_open.visible = closure < 0.5;
    }
    return true;
  };
  const expressions = model.getExpressions();
  const motions = model.getMotions();
  const expressionIds = new Set(
    expressions
      .map((entry) => entry?.name || entry?.expressionId)
      .filter((value) => typeof value === "string" && value),
  );
  const motionKeys = new Set(
    motions.map((entry) => `${entry?.group}:${Number(entry?.no)}`),
  );
  let activeExpressionId = null;
  let presentationRevision = 0;
  let renderedPresentationRevision = 0;
  let explicitRenderCount = 0;
  window.onepicLive2D = {
    getStatus: () => ({
      ready: true,
      profile: animationProfile,
      parameterRanges,
      parameterDefaults: Object.fromEntries(
        Object.entries(parameterDefaults).filter(
          ([id]) =>
            PARAMETER_IDS.includes(id) ||
            Object.prototype.hasOwnProperty.call(persistentParameters, id),
        ),
      ),
      persistentParameters,
      persistentParameterValues: currentPersistentParameterValues(),
      persistentApplyCount,
      gazeMoveCount,
      currentGaze,
      currentHead,
      paused: animationPaused,
      renderer: document.documentElement.dataset.live2dRenderer,
      eyeOpen: currentEyeOpen,
      blinkCount: automaticBlinkCount,
      blinkActive: automaticBlinkActive,
      headOpenVisible: layeredIdle
        ? layeredIdle.sprites.head_open.visible
        : null,
      headClosedVisible: layeredIdle
        ? layeredIdle.sprites.head_closed.visible
        : null,
      headClosedAlpha: layeredIdle
        ? layeredIdle.sprites.head_closed.alpha
        : null,
      blinkClosure: currentBlinkClosure,
      blinkRegionCount: layeredIdle
        ? layeredIdle.blinkRegions.length
        : 0,
      modelVisible: model.visible,
      modelRenderable: model.renderable,
      activeExpression: activeExpressionId,
      presentationRevision,
      renderedPresentationRevision,
      explicitRenderCount,
      accessoryReady: cubismAccessories !== null,
      accessoryLayerCount: cubismAccessories?.entries.length || 0,
      accessoryRoles: cubismAccessories?.roles || [],
      accessories: describeCubismAccessories(model, cubismAccessories),
      catalog: { expressions, motions },
    }),
    getCatalog: () => ({ expressions, motions }),
    setPaused: (paused) => {
      animationPaused = Boolean(paused);
      if (animationPaused) {
        neutralParameters();
      }
      return animationPaused;
    },
    neutralize: () => {
      neutralParameters();
      activeExpressionId = null;
      presentationRevision += 1;
      return true;
    },
    setParameters: (values) => {
      const applied = {};
      for (const [id, value] of Object.entries(values || {})) {
        applied[id] = setParameter(id, Number(value));
      }
      if (Object.keys(applied).length > 0) {
        presentationRevision += 1;
      }
      applyPersistentParameters();
      return applied;
    },
    setExpression: (expressionId) => {
      if (!expressionIds.has(expressionId)) {
        return false;
      }
      model.setExpression({ expressionId });
      applyPersistentParameters();
      activeExpressionId = expressionId;
      presentationRevision += 1;
      return true;
    },
    startMotion: async (group, no, priority) => {
      const motionNo = Number(no);
      if (!motionKeys.has(`${group}:${motionNo}`)) {
        return false;
      }
      await model.startMotion({
        group,
        no: motionNo,
        ...(priority === undefined ? {} : { priority }),
      });
      applyPersistentParameters();
      return true;
    },
    renderNow: () => {
      applyPersistentParameters();
      app.render();
      renderedPresentationRevision = presentationRevision;
      explicitRenderCount += 1;
      return true;
    },
    setIntensity: (value) => {
      const parsed = Number(value);
      if (Number.isFinite(parsed)) {
        animationProfile.intensity = Math.max(0.25, Math.min(1.8, parsed));
        document.documentElement.dataset.live2dProfile =
          JSON.stringify(animationProfile);
      }
      return animationProfile.intensity;
    },
    setLayeredBlink: (closed) => {
      if (!layeredIdle) {
        return false;
      }
      const applied = applyLayeredBlink(closed ? 1 : 0);
      // QA 强制开闭眼时动画可处于暂停状态；显式提交当前舞台，避免截图
      // 偶然依赖下一次 ticker 渲染而得到上一帧。
      app.render();
      return applied;
    },
  };

  let baseScale = 1;
  const resize = () => {
    baseScale = fitDisplayObject(
      app,
      displayObject,
      sourceWidth,
      sourceHeight,
      animationProfile.display_scale,
      animationProfile.display_offset_y_ratio,
    );
    if (cubismAccessories?.binding) {
      // 新视口会改变 Cubism 的 viewport/MVP。等下一次模型 onRender 完成后
      // 再锁定新的参考网格，不能在 resize 回调中读取上一视口的矩阵。
      resetCubismAccessoryBinding(cubismAccessories);
    } else if (
      cubismAccessories &&
      !layoutCubismAccessories(app, model, cubismAccessories)
    ) {
      throw new Error("常驻配件无法取得有效 Cubism 模型边界");
    }
  };
  resize();
  window.addEventListener("resize", resize);
  if (cubismAccessories) {
    // 锁定中性、睁眼的稳定 ArtMesh 集，后续表情只变形同一组顶点。
    neutralParameters();
    await new Promise((resolve) => {
      requestAnimationFrame(() => requestAnimationFrame(resolve));
    });
    cubismAccessories.contentBoundsRatios =
      detectCubismContentBounds(app, model);
    resetCubismAccessoryBinding(cubismAccessories);
    if (
      !cubismAccessories.contentBoundsRatios ||
      !layoutCubismAccessories(app, model, cubismAccessories)
    ) {
      throw new Error("常驻配件无法校准 Cubism 可见内容边界");
    }
    document.body.appendChild(cubismAccessories.root);
    setStage("cubism-accessories-ready");
  }

  // 固定外观参数在每次模型更新前后都重施：前置保证首帧不闪出水印，
  // 后置抵消 motion/expression 对下一帧的残留。人物绘制后再采样同一帧
  // Cubism Part 顶点并提交挂件矩阵，不会创建第二个 Live2D。
  if (cubismAccessories || Object.keys(persistentParameters).length > 0) {
    const renderModelFrame = model.onRender;
    model.onRender = (renderer) => {
      applyPersistentParameters();
      renderModelFrame(renderer);
      applyPersistentParameters();
      if (
        cubismAccessories &&
        !layoutCubismAccessories(app, model, cubismAccessories)
      ) {
        if (cubismAccessories.binding && !cubismAccessories.binding.status) {
          cubismAccessories.binding.status = "waiting-for-cubism-frame";
        }
      }
    };
  }

  const animationStartedAt = performance.now();
  const blinkIntervals = [0.35, 0.82, 0.15, 0.63];
  let blinkIndex = 1;
  let blinkStartedAt = null;
  const [firstBlinkMinimum, firstBlinkMaximum] =
    animationProfile.blink_interval_ms;
  let nextBlinkAt =
    firstBlinkMinimum +
    (firstBlinkMaximum - firstBlinkMinimum) * blinkIntervals[0];
  app.ticker.add(() => {
    const now = performance.now();
    if (animationPaused || !animationProfile.enabled) {
      return;
    }
    const elapsed = now - animationStartedAt;
    const breath = Math.sin(elapsed / 690);
    const sway = Math.sin(elapsed / 1650);
    const slowSway = Math.sin(elapsed / 2350 + 0.7);
    const hairSway = Math.sin(elapsed / 880 + 1.1);
    const gazeTarget = sampleLivelyGaze(elapsed, "eyeTransitionMs");
    // 眼神先到目标，头部晚 180 ms 并用更长缓动追上，避免机械同相摆动。
    const headTarget = sampleLivelyGaze(
      elapsed - 180,
      "headTransitionMs",
    );

    if (animationProfile.head_motion) {
      setNormalizedParameter(
        "ParamAngleX",
        headTarget.x + sway * 0.06,
        0.36,
      );
      setNormalizedParameter(
        "ParamAngleY",
        headTarget.y + slowSway * 0.04,
        0.18,
      );
      setNormalizedParameter(
        "ParamAngleZ",
        headTarget.z + Math.sin(elapsed / 2100) * 0.05,
        0.12,
      );
      currentHead = { ...headTarget };
    } else {
      currentHead = { index: 0, x: 0, y: 0, z: 0 };
    }
    if (animationProfile.eye_motion) {
      setNormalizedParameter("ParamEyeBallX", gazeTarget.x, 0.62);
      setNormalizedParameter(
        "ParamEyeBallY",
        gazeTarget.y,
        0.45,
      );
      if (
        lastGazeTargetIndex >= 0 &&
        gazeTarget.index !== lastGazeTargetIndex
      ) {
        gazeMoveCount += 1;
      }
      lastGazeTargetIndex = gazeTarget.index;
      currentGaze = {
        index: gazeTarget.index,
        x: gazeTarget.x,
        y: gazeTarget.y,
      };
    } else {
      currentGaze = { index: 0, x: 0, y: 0 };
    }
    if (animationProfile.body_motion) {
      setNormalizedParameter("ParamBodyAngleX", slowSway, 0.32);
      setNormalizedParameter(
        "ParamBodyAngleY",
        Math.sin(elapsed / 2450 + 0.2),
        0.16,
      );
      setNormalizedParameter("ParamBodyAngleZ", sway, 0.24);
      setUnitParameter("ParamBreath", 0.5 + breath * 0.5);
    }
    if (animationProfile.hair_motion) {
      setNormalizedParameter("ParamHairFront", hairSway, 0.36);
      setNormalizedParameter(
        "ParamHairSide",
        Math.sin(elapsed / 1050 + 0.4),
        0.48,
      );
      setNormalizedParameter(
        "ParamHairBack",
        Math.sin(elapsed / 1250 + 1.8),
        0.32,
      );
    }

    let eyeOpen = 1;
    if (animationProfile.blink) {
      if (blinkStartedAt === null && elapsed >= nextBlinkAt) {
        blinkStartedAt = elapsed;
        automaticBlinkCount += 1;
      }
      if (blinkStartedAt !== null) {
        const blinkElapsed = elapsed - blinkStartedAt;
        const [closeMs, holdMs, openMs] =
          animationProfile.blink_duration_ms;
        const holdEndsAt = closeMs + holdMs;
        const blinkEndsAt = holdEndsAt + openMs;
        if (blinkElapsed < closeMs) {
          eyeOpen = 1 - blinkElapsed / closeMs;
        } else if (blinkElapsed < holdEndsAt) {
          eyeOpen = 0;
        } else if (blinkElapsed < blinkEndsAt) {
          eyeOpen = (blinkElapsed - holdEndsAt) / openMs;
        } else {
          blinkStartedAt = null;
          const [minimum, maximum] = animationProfile.blink_interval_ms;
          const fraction = blinkIntervals[blinkIndex % blinkIntervals.length];
          blinkIndex += 1;
          nextBlinkAt = elapsed + minimum + (maximum - minimum) * fraction;
          eyeOpen = 1;
        }
      }
      const eyeParameterValue =
        animationProfile.blink_parameter_mode === "binary"
          ? eyeOpen < 0.5
            ? 0
            : 1
          : eyeOpen;
      setEyeOpenFraction("ParamEyeLOpen", eyeParameterValue);
      setEyeOpenFraction("ParamEyeROpen", eyeParameterValue);
      eyeOpen = eyeParameterValue;
    }
    currentEyeOpen = eyeOpen;
    automaticBlinkActive = blinkStartedAt !== null;

    if (layeredIdle) {
      const { sprites } = layeredIdle;
      const headRotation = animationProfile.head_motion
        ? sway * 0.032 * animationProfile.intensity
        : 0;
      const headY = animationProfile.head_motion
        ? breath * 3.4 * animationProfile.intensity
        : 0;
      for (const head of [sprites.head_open, sprites.head_closed]) {
        head.rotation = headRotation;
        head.position.y = head.pivot.y + headY;
        head.scale.set(
          animationProfile.head_motion
            ? 1 + breath * 0.006 * animationProfile.intensity
            : 1,
        );
      }
      if (layeredIdle.blinkMask) {
        layeredIdle.blinkMask.rotation = headRotation;
        layeredIdle.blinkMask.position.y =
          layeredIdle.blinkMask.pivot.y + headY;
        layeredIdle.blinkMask.scale.copyFrom(sprites.head_closed.scale);
      }
      // 完整睁眼头始终稳定；闭眼图只通过两侧眼形椭圆逐步覆盖。
      // 因此慢眨既有真实闭合过程，也不会替换帽子、头发或整张脸。
      const blinkClosure = Math.max(0, Math.min(1, 1 - eyeOpen));
      applyLayeredBlink(blinkClosure);

      const bodyEnabled = animationProfile.body_motion;
      sprites.body.scale.set(
        bodyEnabled
          ? 1 + breath * 0.006 * animationProfile.intensity
          : 1,
        bodyEnabled
          ? 1 + breath * 0.018 * animationProfile.intensity
          : 1,
      );
      sprites.body.rotation = bodyEnabled
        ? slowSway * 0.010 * animationProfile.intensity
        : 0;
      sprites.arm_left.rotation = bodyEnabled
        ? (sway * 0.024 + breath * 0.006) * animationProfile.intensity
        : 0;
      sprites.arm_right.rotation = bodyEnabled
        ? (-sway * 0.024 + breath * 0.006) * animationProfile.intensity
        : 0;
      sprites.arm_left.position.y = sprites.arm_left.pivot.y +
        (bodyEnabled ? breath * 2.0 * animationProfile.intensity : 0);
      sprites.arm_right.position.y = sprites.arm_right.pivot.y +
        (bodyEnabled ? breath * 2.0 * animationProfile.intensity : 0);
      if (sprites.accessory) {
        sprites.accessory.rotation = bodyEnabled ? -headRotation * 0.18 : 0;
        sprites.accessory.position.y = sprites.accessory.pivot.y +
          (bodyEnabled ? breath * 1.2 * animationProfile.intensity : 0);
      }
    }

    applyPersistentParameters();
    if (!animationProfile.root_motion) {
      return;
    }
    displayObject.position.set(
      app.screen.width / 2,
      app.screen.height / 2
        + 2
        + app.screen.height * animationProfile.display_offset_y_ratio
        + breath * 2.4 * animationProfile.intensity,
    );
    displayObject.rotation = sway * 0.008 * animationProfile.intensity;
    displayObject.scale.set(
      baseScale * (1 + breath * 0.010 * animationProfile.intensity),
    );
  });
  if (blinkUrl) {
    console.info("眨眼叠层已登记；当前由混合精灵状态提供表情反馈");
    setStage("blink-sprite-fallback");
  }
  status.textContent = "";
  document.documentElement.dataset.live2dReady = "true";
}

start().catch((error) => {
  const detail = error?.stack || String(error);
  document.documentElement.dataset.live2dReady = "false";
  status.textContent = `Live2D 回退：${error.message}`;
  document.documentElement.dataset.live2dError = detail;
});
