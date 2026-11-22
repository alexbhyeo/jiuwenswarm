import '@google/model-viewer';
import { useEffect, useRef, useState } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { collectBoneNodes, collectBoneSegments } from './skeletonOverlay';

/** 骨骼线 = 浅色细线、关节 = 亮紫色小点，都关掉深度测试，所以能像
 *  Tripo3D 的预览图那样"透过"网格看到骨架（见用户给的参考截图）。 */
const BONE_LINE_COLOR = 0xdfe9ff;
const BONE_JOINT_COLOR = 0x8b7cff;
const JOINT_PIXEL_SIZE = 4;

type SkeletonModelViewerProps = {
  src: string;
  alt: string;
  className?: string;
  /** 是否叠加骨骼覆盖层。自动绑定产出的模型传 true。 */
  showSkeleton?: boolean;
  /** 是否可以拖拽旋转/缩放。缩略图不开启（点击用来放大查看），大图弹窗开启。 */
  interactive?: boolean;
  onActivate?: () => void;
  testId?: string;
};

type LoadState = 'loading' | 'ready' | 'failed';

/**
 * 带骨骼覆盖层的 GLB 预览器。
 *
 * `<model-viewer>` 没有暴露"取到骨架"的稳定 API，所以自动绑定的产出改用
 * 这里直接搭的 three.js 场景：网格 + SkeletonHelper 式的骨骼线段 + 关节
 * 标记，按需渲染（静态模型只在加载完成/相机变化/尺寸变化时各画一帧，
 * 有动画的模型才持续播放）。
 *
 * 加载失败（或该环境拿不到 WebGL）时回退到 `<model-viewer>`，至少还能看到
 * 模型本身，不会变成一个空白框。
 */
export function SkeletonModelViewer({
  src,
  alt,
  className,
  showSkeleton = true,
  interactive = false,
  onActivate,
  testId,
}: SkeletonModelViewerProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const timerRef = useRef<THREE.Timer | null>(null);
  const [state, setState] = useState<LoadState>('loading');
  const [boneCount, setBoneCount] = useState(0);
  const [animated, setAnimated] = useState(false);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    } catch {
      setState('failed');
      return;
    }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.domElement.className = 'game-lab-skeleton-canvas';
    container.appendChild(renderer.domElement);

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(35, 1, 0.01, 1000);
    camera.position.set(0, 0.6, 3);
    scene.add(new THREE.HemisphereLight(0xffffff, 0x3a3a44, 2.6));
    const keyLight = new THREE.DirectionalLight(0xffffff, 2.2);
    keyLight.position.set(3, 5, 4);
    scene.add(keyLight);
    const fillLight = new THREE.DirectionalLight(0xffffff, 1);
    fillLight.position.set(-4, 2, -3);
    scene.add(fillLight);

    const controls = interactive ? new OrbitControls(camera, renderer.domElement) : null;
    if (controls) {
      controls.enableDamping = false;
      controls.enablePan = false;
      controls.minDistance = 0.2;
      controls.maxDistance = 40;
    }

    let mixer: THREE.AnimationMixer | null = null;
    let frameId = 0;
    let disposed = false;
    const disposables: Array<{ dispose: () => void }> = [];
    const resize = () => {
      const width = Math.max(container.clientWidth, 1);
      const height = Math.max(container.clientHeight, 1);
      renderer.setSize(width, height, false);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
    };
    const renderFrame = () => renderer.render(scene, camera);

    const observer = new ResizeObserver(() => {
      resize();
      renderFrame();
    });
    observer.observe(container);
    resize();

    /** 骨骼线段 + 关节标记：每帧从骨头的 matrixWorld 取世界坐标重算，
     *  所以有动画的模型（重定向产出）骨点会跟着动。 */
    const buildSkeletonOverlay = (root: THREE.Object3D) => {
      const traversal = {
        isBone: (node: THREE.Object3D) => (node as THREE.Bone).isBone === true,
        children: (node: THREE.Object3D) => node.children,
      };
      const segments = collectBoneSegments(root, traversal);
      const bones = collectBoneNodes(root, traversal);
      setBoneCount(bones.length);
      if (bones.length === 0) return null;

      const linePositions = new Float32Array(segments.length * 2 * 3);
      const lineGeometry = new THREE.BufferGeometry();
      lineGeometry.setAttribute('position', new THREE.BufferAttribute(linePositions, 3));
      const lineMaterial = new THREE.LineBasicMaterial({
        color: BONE_LINE_COLOR,
        transparent: true,
        opacity: 0.95,
        depthTest: false,
      });
      const lines = new THREE.LineSegments(lineGeometry, lineMaterial);
      lines.renderOrder = 10;
      scene.add(lines);

      const jointPositions = new Float32Array(bones.length * 3);
      const jointGeometry = new THREE.BufferGeometry();
      jointGeometry.setAttribute('position', new THREE.BufferAttribute(jointPositions, 3));
      const jointMaterial = new THREE.PointsMaterial({
        color: BONE_JOINT_COLOR,
        size: JOINT_PIXEL_SIZE,
        sizeAttenuation: false,
        transparent: true,
        depthTest: false,
      });
      const joints = new THREE.Points(jointGeometry, jointMaterial);
      joints.renderOrder = 11;
      scene.add(joints);

      disposables.push(lineGeometry, lineMaterial, jointGeometry, jointMaterial);

      const point = new THREE.Vector3();
      const update = () => {
        segments.forEach(([parent, child], index) => {
          parent.getWorldPosition(point);
          linePositions.set([point.x, point.y, point.z], index * 6);
          child.getWorldPosition(point);
          linePositions.set([point.x, point.y, point.z], index * 6 + 3);
        });
        bones.forEach((bone, index) => {
          bone.getWorldPosition(point);
          jointPositions.set([point.x, point.y, point.z], index * 3);
        });
        lineGeometry.attributes.position.needsUpdate = true;
        jointGeometry.attributes.position.needsUpdate = true;
      };
      update();
      return update;
    };

    const loader = new GLTFLoader();
    loader.load(
      src,
      (gltf) => {
        if (disposed) return;
        scene.add(gltf.scene);

        const box = new THREE.Box3().setFromObject(gltf.scene);
        const size = box.getSize(new THREE.Vector3());
        const center = box.getCenter(new THREE.Vector3());
        const maxDimension = Math.max(size.x, size.y, size.z) || 1;
        const distance = (maxDimension / 2 / Math.tan((camera.fov * Math.PI) / 360)) * 1.35;
        const direction = new THREE.Vector3(0.6, 0.45, 1).normalize();
        camera.position.copy(center).addScaledVector(direction, distance);
        camera.near = Math.max(distance / 100, 0.001);
        camera.far = distance * 100;
        camera.updateProjectionMatrix();
        if (controls) {
          controls.target.copy(center);
          controls.update();
        } else {
          camera.lookAt(center);
        }

        const updateOverlay = showSkeleton ? buildSkeletonOverlay(gltf.scene) : null;
        if (gltf.animations.length > 0) {
          mixer = new THREE.AnimationMixer(gltf.scene);
          mixer.clipAction(gltf.animations[0]).play();
          setAnimated(true);
        } else {
          setAnimated(false);
        }

        setState('ready');
        renderFrame();

        if (mixer) {
          // 只有带动画的模型才需要持续刷新；静态模型按需渲染即可。
          // 用 Timer 而不是已弃用的 Clock（three 0.183 起 Clock 会打警告），
          // 它还能配合 Page Visibility 避免切走标签页后回来跳一大帧。
          const timer = new THREE.Timer();
          timer.connect(document);
          const animate = (timestamp: number) => {
            if (disposed) return;
            timer.update(timestamp);
            mixer?.update(timer.getDelta());
            updateOverlay?.();
            renderFrame();
            frameId = requestAnimationFrame(animate);
          };
          frameId = requestAnimationFrame(animate);
          timerRef.current = timer;
        } else if (controls) {
          controls.addEventListener('change', renderFrame);
        }
      },
      undefined,
      () => {
        if (!disposed) setState('failed');
      }
    );

    return () => {
      disposed = true;
      if (frameId) cancelAnimationFrame(frameId);
      timerRef.current?.disconnect();
      timerRef.current = null;
      observer.disconnect();
      controls?.removeEventListener('change', renderFrame);
      controls?.dispose();
      mixer?.stopAllAction();
      disposables.forEach((item) => item.dispose());
      renderer.dispose();
      renderer.forceContextLoss();
      renderer.domElement.remove();
    };
  }, [src, showSkeleton, interactive]);

  return (
    <div
      ref={containerRef}
      className={className}
      onClick={onActivate}
      data-testid={testId}
      data-status={state}
      data-bones={boneCount}
      data-animated={animated ? 'true' : 'false'}
      data-has-skeleton={showSkeleton && boneCount > 0 ? 'true' : 'false'}
    >
      {/* 加载失败时退回到 model-viewer：至少还能看到模型本身。 */}
      {state === 'failed' && (
        <model-viewer src={src} alt={alt} className="game-lab-node-model-thumb" style={{ width: '100%', height: '100%' }} />
      )}
    </div>
  );
}
