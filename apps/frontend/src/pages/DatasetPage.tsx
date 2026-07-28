import { useEffect, useMemo, useRef, useState } from "react";
import { DownloadOutlined } from "@ant-design/icons";
import { Button, Card, Form, Input, Modal, Progress, Select, Segmented, Space, Statistic, Table, Tag, message } from "antd";
import { ThunderboltOutlined } from "@ant-design/icons";

import {
  DatasetAnnotation,
  DatasetImage,
  DatasetSummary,
  EntityType,
  SystemCatalog,
  VisionAnalyzeResponse,
  VisionRefineRequest,
  VisionRefineResponse,
  YoloExportPlan,
  fetchJson,
  postJson,
} from "../api";

type DraftBox = { x1: number; y1: number; x2: number; y2: number };
type DraftPoint = { x: number; y: number };

function readAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

export function DatasetPage() {
  const [images, setImages] = useState<DatasetImage[]>([]);
  const [annotations, setAnnotations] = useState<DatasetAnnotation[]>([]);
  const [summary, setSummary] = useState<DatasetSummary>();
  const [systems, setSystems] = useState<SystemCatalog[]>([]);
  const [entityTypes, setEntityTypes] = useState<EntityType[]>([]);
  const [exportPlan, setExportPlan] = useState<YoloExportPlan>();
  const [aiPreview, setAiPreview] = useState<VisionAnalyzeResponse>();
  const [refinePreview, setRefinePreview] = useState<VisionRefineResponse>();
  const [aiLoading, setAiLoading] = useState(false);
  const [refineLoading, setRefineLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [selectedImageId, setSelectedImageId] = useState<number>();
  const [selectedEntityTypeId, setSelectedEntityTypeId] = useState<number>();
  const [annotationMode, setAnnotationMode] = useState<"bbox" | "polygon">("bbox");
  const [draftBox, setDraftBox] = useState<DraftBox>();
  const [draftPolygon, setDraftPolygon] = useState<DraftPoint[]>([]);
  const [drawingStart, setDrawingStart] = useState<{ x: number; y: number }>();
  const imageRef = useRef<HTMLImageElement>(null);
  const [form] = Form.useForm();
  const [messageApi, contextHolder] = message.useMessage();

  const loadData = () => {
    void fetchJson<DatasetImage[]>("/api/datasets/images").then((items) => {
      setImages(items);
      if (!selectedImageId && items[0]) setSelectedImageId(items[0].id);
    });
    void fetchJson<DatasetAnnotation[]>("/api/datasets/annotations").then(setAnnotations);
    void fetchJson<DatasetSummary>("/api/datasets/summary").then(setSummary);
    void fetchJson<SystemCatalog[]>("/api/metadata/systems").then(setSystems);
    void fetchJson<EntityType[]>("/api/metadata/entity-types").then(setEntityTypes);
    void fetchJson<YoloExportPlan>("/api/datasets/exports/yolo-plan").then(setExportPlan);
  };

  useEffect(() => {
    loadData();
  }, []);

  const systemNameById = useMemo(() => new Map(systems.map((item) => [item.id, item.name])), [systems]);
  const selectedImage = images.find((item) => item.id === selectedImageId);
  const imageAnnotations = annotations.filter((item) => item.image_id === selectedImageId);
  const selectedImageWidth = selectedImage?.width || 1;
  const selectedImageHeight = selectedImage?.height || 1;
  const componentOptions = entityTypes
    .filter((item) => item.category === "component")
    .map((item) => ({ value: item.id, label: `${item.name} (${item.code})` }));

  const createImage = async () => {
    const values = await form.validateFields();
    await postJson<DatasetImage>("/api/datasets/images", values);
    messageApi.success("图片记录已加入数据池");
    form.resetFields();
    setModalOpen(false);
    loadData();
  };

  const uploadLocalImage = async (file: File) => {
    const imageDataUrl = await readAsDataUrl(file);
    const image = new Image();
    image.onload = async () => {
      const created = await postJson<DatasetImage>("/api/datasets/images", {
        file_name: file.name,
        source_type: "manual_upload",
        width: image.width,
        height: image.height,
        image_data_url: imageDataUrl,
      });
      setSelectedImageId(created.id);
      messageApi.success("图片已上传到数据池");
      loadData();
    };
    image.src = imageDataUrl;
  };

  const runAiPreview = async () => {
    if (!selectedImage?.image_data_url) {
      messageApi.warning("请先选择一张带图片内容的数据");
      return;
    }
    setAiLoading(true);
    try {
      const response = await postJson<VisionAnalyzeResponse>("/api/vision/analyze", {
        file_name: selectedImage.file_name,
        image_data_url: selectedImage.image_data_url,
        confidence: 0.2,
        iou: 0.7,
        image_size: 960,
      });
      setAiPreview(response);
      messageApi.success("AI 预提议已生成");
    } catch (error) {
      messageApi.error(error instanceof Error ? error.message : "AI 预提议失败");
    } finally {
      setAiLoading(false);
    }
  };

  const refineDraft = async () => {
    if (!selectedImage?.image_data_url || !draftBox) {
      messageApi.warning("请先绘制一个粗略方框");
      return;
    }
    setRefineLoading(true);
    try {
      const payload: VisionRefineRequest = {
        file_name: selectedImage.file_name,
        image_data_url: selectedImage.image_data_url,
        bbox: [
          Math.round(Math.min(draftBox.x1, draftBox.x2)),
          Math.round(Math.min(draftBox.y1, draftBox.y2)),
          Math.round(Math.max(draftBox.x1, draftBox.x2)),
          Math.round(Math.max(draftBox.y1, draftBox.y2)),
        ],
        iterations: 5,
      };
      const response = await postJson<VisionRefineResponse>("/api/vision/refine", payload);
      setRefinePreview(response);
      setAnnotationMode("polygon");
      setDraftBox(undefined);
      setDraftPolygon(
        response.polygon.reduce<DraftPoint[]>((acc, value, index) => {
          if (index % 2 === 0) {
            acc.push({ x: value, y: response.polygon[index + 1] });
          }
          return acc;
        }, []),
      );
      messageApi.success("已贴合为轮廓草稿");
    } catch (error) {
      messageApi.error(error instanceof Error ? error.message : "轮廓精修失败");
    } finally {
      setRefineLoading(false);
    }
  };

  const applyPreviewBox = (bbox: number[]) => {
    if (bbox.length !== 4) return;
    setAnnotationMode("bbox");
    setDraftPolygon([]);
    setDrawingStart(undefined);
    setDraftBox({
      x1: bbox[0],
      y1: bbox[1],
      x2: bbox[2],
      y2: bbox[3],
    });
    messageApi.info("已把 AI 预提议框放入草稿");
  };

  const pointFromMouse = (event: React.MouseEvent<HTMLDivElement>) => {
    const image = imageRef.current;
    if (!image || !selectedImage?.width || !selectedImage?.height) return null;
    const rect = image.getBoundingClientRect();
    const scaleX = selectedImage.width / rect.width;
    const scaleY = selectedImage.height / rect.height;
    return {
      x: Math.max(0, Math.min(selectedImage.width, (event.clientX - rect.left) * scaleX)),
      y: Math.max(0, Math.min(selectedImage.height, (event.clientY - rect.top) * scaleY)),
    };
  };

  const saveAnnotation = async () => {
    if (!selectedImage || !selectedEntityTypeId) {
      messageApi.warning("请先选择图片和零部件类型");
      return;
    }
    const annotationType = annotationMode;
    const geometry =
      annotationType === "bbox"
        ? draftBox
          ? [
              Math.round(Math.min(draftBox.x1, draftBox.x2)),
              Math.round(Math.min(draftBox.y1, draftBox.y2)),
              Math.round(Math.max(draftBox.x1, draftBox.x2)),
              Math.round(Math.max(draftBox.y1, draftBox.y2)),
            ]
          : null
        : draftPolygon.length >= 3
          ? draftPolygon.flatMap((point) => [Math.round(point.x), Math.round(point.y)])
          : null;
    if (!geometry) {
      messageApi.warning(annotationType === "bbox" ? "请先绘制方框" : "请先至少点击 3 个点形成轮廓");
      return;
    }
    if (annotationType === "bbox" && (geometry[2] - geometry[0] < 5 || geometry[3] - geometry[1] < 5)) {
      messageApi.warning("方框太小，请重新绘制");
      return;
    }
    await postJson<DatasetAnnotation>("/api/datasets/annotations", {
      image_id: selectedImage.id,
      entity_type_id: selectedEntityTypeId,
      bbox: geometry,
      annotation_type: annotationType,
    });
    setDraftBox(undefined);
    setDraftPolygon([]);
    messageApi.success("标注已保存");
    loadData();
  };

  const renderBox = (bbox: number[], key: string, label?: string, tone: "annotation" | "proposal" = "annotation") => {
    if (!selectedImage?.width || !selectedImage?.height) return null;
    const [x1, y1, x2, y2] = bbox;
    return (
      <div
        key={key}
        className={`annotation-box ${tone === "proposal" ? "annotation-box-proposal" : ""}`}
        style={{
          left: `${(x1 / selectedImage.width) * 100}%`,
          top: `${(y1 / selectedImage.height) * 100}%`,
          width: `${((x2 - x1) / selectedImage.width) * 100}%`,
          height: `${((y2 - y1) / selectedImage.height) * 100}%`,
        }}
      >
        {label && <span>{label}</span>}
      </div>
    );
  };

  const renderPolygon = (points: number[], key: string, label?: string, draft = false) => {
    if (!selectedImage?.width || !selectedImage?.height || points.length < 6) return null;
    const coordPairs = points.reduce<Array<{ x: number; y: number }>>((acc, value, index) => {
      if (index % 2 === 0) {
        acc.push({ x: value, y: points[index + 1] });
      }
      return acc;
    }, []);
    const polyPoints = coordPairs.map((point) => `${(point.x / selectedImageWidth) * 100},${(point.y / selectedImageHeight) * 100}`).join(" ");
    return (
      <svg key={key} className="annotation-polygon" viewBox="0 0 100 100" preserveAspectRatio="none">
        <polygon
          points={polyPoints}
          className={draft ? "annotation-polygon-draft" : "annotation-polygon-shape"}
        />
        {label && (
          <text x="2" y="6" className="annotation-polygon-label">
            {label}
          </text>
        )}
      </svg>
    );
  };

  const addPolygonPoint = (point: DraftPoint) => {
    setDraftPolygon((current) => [...current, point]);
  };

  return (
    <div className="page">
      {contextHolder}
      <div className="page-title">
        <div>
          <h2>数据集标注平台</h2>
          <p>上传底盘图片，在图片上绘制方框或轮廓并选择零部件类型，用于构建 YOLO-Seg 分割数据集。</p>
        </div>
        <Space>
          <Button onClick={() => setModalOpen(true)}>登记图片</Button>
          <Button type="primary" icon={<DownloadOutlined />} href="/api/datasets/exports/yolo-seg.zip">
            导出 YOLO-Seg 数据集
          </Button>
          <Button icon={<DownloadOutlined />} href="/api/datasets/exports/coco-seg.zip">
            导出 COCO 分割数据集
          </Button>
        </Space>
      </div>

      <div className="metric-grid">
        <Card><Statistic title="图片总数" value={summary?.image_count || 0} /></Card>
        <Card><Statistic title="未标注" value={summary?.unlabeled_count || 0} /></Card>
        <Card><Statistic title="标注中" value={summary?.labeling_count || 0} /></Card>
        <Card><Statistic title="已审核" value={summary?.reviewed_count || 0} /></Card>
      </div>

      <div className="two-column">
        <Space direction="vertical" size={16} style={{ width: "100%" }}>
          <Card title="图片池">
            <input
              type="file"
              accept="image/*"
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) void uploadLocalImage(file);
                event.currentTarget.value = "";
              }}
            />
            <Table
              rowKey="id"
              size="small"
              dataSource={images}
              rowClassName={(record) => record.id === selectedImageId ? "selected-row" : ""}
              onRow={(record) => ({ onClick: () => setSelectedImageId(record.id) })}
              columns={[
                { title: "ID", dataIndex: "id", width: 60 },
                { title: "文件名", dataIndex: "file_name" },
                { title: "系统", dataIndex: "system_id", render: (value) => value ? systemNameById.get(value) : "-" },
                { title: "状态", dataIndex: "annotation_status", render: (value) => <Tag>{value}</Tag> },
                { title: "对象", dataIndex: "object_count" },
              ]}
            />
          </Card>

          <Card title="类别覆盖">
            <Table
              rowKey="entity_type_id"
              size="small"
              pagination={false}
              dataSource={summary?.class_stats || []}
              columns={[
                { title: "类别", dataIndex: "entity_type_name" },
                { title: "系统", dataIndex: "system_name" },
                {
                  title: "进度",
                  render: (_, row) => (
                    <Progress
                      percent={Math.min(100, Math.round((row.labeled_instances / row.target_instances) * 100))}
                      size="small"
                      format={() => `${row.labeled_instances}/${row.target_instances}`}
                    />
                  ),
                },
              ]}
            />
          </Card>
        </Space>

        <Space direction="vertical" size={16} style={{ width: "100%" }}>
          <Card
            title="标注工作台"
            extra={
              <Space>
                <Segmented
                  value={annotationMode}
                  onChange={(value) => {
                    setAnnotationMode(value as "bbox" | "polygon");
                    setDraftBox(undefined);
                    setDraftPolygon([]);
                    setDrawingStart(undefined);
                  }}
                  options={[
                    { label: "方框", value: "bbox" },
                    { label: "轮廓", value: "polygon" },
                  ]}
                />
                <Select
                  style={{ width: 260 }}
                  placeholder="选择零部件类型"
                  value={selectedEntityTypeId}
                  onChange={setSelectedEntityTypeId}
                  options={componentOptions}
                />
                <Button icon={<ThunderboltOutlined />} loading={aiLoading} onClick={runAiPreview}>
                  AI 预提议
                </Button>
                <Button loading={refineLoading} onClick={refineDraft} disabled={!draftBox}>
                  自动贴合轮廓
                </Button>
                <Button onClick={() => { setDraftBox(undefined); setDraftPolygon([]); setDrawingStart(undefined); }}>清除草稿</Button>
                <Button type="primary" onClick={saveAnnotation}>保存标注</Button>
              </Space>
            }
          >
            {selectedImage?.image_data_url ? (
              <div
                className="annotation-canvas"
                onMouseDown={(event) => {
                  const point = pointFromMouse(event);
                  if (!point) return;
                  if (annotationMode === "bbox") {
                    setDrawingStart(point);
                    setDraftBox({ x1: point.x, y1: point.y, x2: point.x, y2: point.y });
                    return;
                  }
                  addPolygonPoint(point);
                }}
                onMouseMove={(event) => {
                  if (annotationMode !== "bbox" || !drawingStart) return;
                  const point = pointFromMouse(event);
                  if (!point) return;
                  setDraftBox({ x1: drawingStart.x, y1: drawingStart.y, x2: point.x, y2: point.y });
                }}
                onMouseUp={() => setDrawingStart(undefined)}
              >
                <img ref={imageRef} src={selectedImage.image_data_url} alt={selectedImage.file_name} draggable={false} />
                {imageAnnotations.map((item) =>
                  item.annotation_type === "polygon"
                    ? renderPolygon(item.bbox, String(item.id), item.entity_type_name)
                    : renderBox(item.bbox, String(item.id), item.entity_type_name),
                )}
                {aiPreview?.detections.map((item) => renderBox(item.bbox, `preview-${item.id}`, `预提议 ${item.label}`, "proposal"))}
                {annotationMode === "bbox" && draftBox && renderBox([draftBox.x1, draftBox.y1, draftBox.x2, draftBox.y2], "draft", "草稿")}
                {annotationMode === "polygon" && draftPolygon.length >= 1 && (
                  <svg className="annotation-polygon" viewBox="0 0 100 100" preserveAspectRatio="none">
                    <polyline
                      points={draftPolygon.map((point) => `${(point.x / selectedImageWidth) * 100},${(point.y / selectedImageHeight) * 100}`).join(" ")}
                    className="annotation-polygon-draft"
                    />
                    {draftPolygon.map((point, index) => (
                      <circle
                        key={`${point.x}-${point.y}-${index}`}
                        cx={(point.x / selectedImageWidth) * 100}
                        cy={(point.y / selectedImageHeight) * 100}
                        r="1.2"
                        className="annotation-point"
                      />
                    ))}
                  </svg>
                )}
              </div>
            ) : (
              <div className="chart-placeholder">请选择或上传带图片内容的数据</div>
            )}
            {annotationMode === "polygon" && (
              <div style={{ marginTop: 12 }}>
                <Tag color="blue">轮廓模式</Tag>
                <span style={{ marginLeft: 8 }}>依次点击多个点形成零部件轮廓，保存后用于分割训练。</span>
              </div>
            )}
            {refinePreview && (
              <div style={{ marginTop: 12 }}>
                <Tag color="purple">精修结果</Tag>
                <span style={{ marginLeft: 8 }}>mask 覆盖率 {Math.round(refinePreview.mask_coverage * 100)}%</span>
              </div>
            )}
          </Card>

          <Card title="AI 预提议">
            {aiPreview ? (
              <Space direction="vertical" size={12} style={{ width: "100%" }}>
                <Space wrap>
                  <Tag color="purple">{aiPreview.task.detector_name}</Tag>
                  <Tag>对象数 {aiPreview.detections.length}</Tag>
                  <Tag>图片 {aiPreview.image.width} x {aiPreview.image.height}</Tag>
                </Space>
                <Table
                  rowKey="id"
                  size="small"
                  pagination={false}
                  dataSource={aiPreview.detections}
                  columns={[
                    { title: "区域", dataIndex: "label" },
                    { title: "置信度", dataIndex: "confidence", render: (value) => `${Math.round(Number(value) * 100)}%` },
                    { title: "来源", dataIndex: "source" },
                    {
                      title: "操作",
                      render: (_, row) => (
                        <Button size="small" onClick={() => applyPreviewBox(row.bbox)}>
                          放入草稿
                        </Button>
                      ),
                    },
                  ]}
                />
                <div style={{ color: "var(--muted-color, #6b7280)" }}>{aiPreview.ai_summary}</div>
              </Space>
            ) : (
              <div className="chart-placeholder" style={{ height: 120 }}>点击“AI 预提议”先圈出疑似零部件区域</div>
            )}
          </Card>

          <Card title="当前图片标注">
            <Table
              rowKey="id"
              dataSource={imageAnnotations}
              columns={[
                { title: "ID", dataIndex: "id", width: 70 },
                { title: "零部件类型", dataIndex: "entity_type_name" },
                { title: "几何", dataIndex: "bbox", render: (value: number[], record) => `${record.annotation_type}: ${value.join(", ")}` },
                { title: "状态", dataIndex: "status", render: (value) => <Tag>{value}</Tag> },
              ]}
            />
          </Card>

          <Card title="YOLO 导出计划">
            <Space direction="vertical" size={12} style={{ width: "100%" }}>
              <Space>
                <Tag>format: {exportPlan?.format}</Tag>
                <Tag>classes: {exportPlan?.class_count}</Tag>
                <Tag>exportable images: {exportPlan?.image_count}</Tag>
              </Space>
              <div>{exportPlan?.classes.map((item) => <Tag key={item}>{item}</Tag>)}</div>
              <div style={{ color: "var(--muted-color, #6b7280)" }}>{exportPlan?.notes?.join(" ")}</div>
              <div style={{ color: "var(--muted-color, #6b7280)" }}>MMDetection 微调建议使用 COCO 分割导出包。</div>
            </Space>
          </Card>
        </Space>
      </div>

      <Modal title="登记图片到数据池" open={modalOpen} onCancel={() => setModalOpen(false)} onOk={createImage} okText="登记">
        <Form form={form} layout="vertical">
          <Form.Item name="file_name" label="文件名" rules={[{ required: true }]}>
            <Input placeholder="例如：xpeng_x9_front_suspension_002.jpg" />
          </Form.Item>
          <Form.Item name="source_type" label="来源" initialValue="manual_upload" rules={[{ required: true }]}>
            <Select
              options={[
                { value: "manual_upload", label: "手动上传" },
                { value: "web_research", label: "公开资料调研" },
                { value: "video_frame", label: "视频抽帧" },
                { value: "workshop", label: "维修/拆解现场" },
                { value: "synthetic", label: "合成数据" },
              ]}
            />
          </Form.Item>
          <Form.Item name="vehicle_hint" label="车型线索">
            <Input placeholder="例如：小鹏 X9" />
          </Form.Item>
          <Form.Item name="system_id" label="系统线索">
            <Select allowClear options={systems.map((item) => ({ value: item.id, label: item.name }))} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
