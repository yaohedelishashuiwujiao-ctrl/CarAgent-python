import { useEffect, useState } from "react";
import { Alert, Button, Card, Col, Descriptions, Empty, Image, Row, Slider, Space, Table, Tag, Upload, message, Typography } from "antd";
import { InboxOutlined, ReloadOutlined, SettingOutlined } from "@ant-design/icons";

import { VisionAnalyzeRequest, VisionAnalyzeResponse, VisionTask, fetchJson, postJson } from "../api";

function readAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

export function VisionPage() {
  const [result, setResult] = useState<VisionAnalyzeResponse>();
  const [tasks, setTasks] = useState<VisionTask[]>([]);
  const [loading, setLoading] = useState(false);
  const [confidence, setConfidence] = useState(0.25);
  const [iou, setIou] = useState(0.7);
  const [imageSize, setImageSize] = useState(640);
  const [messageApi, contextHolder] = message.useMessage();
  const reviewColor = (value: string) => {
    if (value === "needs_review") return "gold";
    if (value === "proposed") return "purple";
    if (value === "auto_accepted") return "green";
    return "default";
  };

  const loadTasks = () => {
    void fetchJson<VisionTask[]>("/api/vision/tasks").then(setTasks);
  };

  useEffect(() => {
    loadTasks();
  }, []);

  const analyzeFile = async (file: File) => {
    setLoading(true);
    try {
      const imageDataUrl = await readAsDataUrl(file);
      const payload: VisionAnalyzeRequest = {
        file_name: file.name,
        image_data_url: imageDataUrl,
        confidence,
        iou,
        image_size: imageSize,
      };
      const response = await postJson<VisionAnalyzeResponse>("/api/vision/analyze", payload);
      setResult(response);
      loadTasks();
      messageApi.success("识别任务完成");
    } catch (error) {
      messageApi.error(error instanceof Error ? error.message : "识别失败");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="page">
      {contextHolder}
      <div className="page-title">
        <div>
          <h2>视觉识别工作台</h2>
          <p>上传底盘图片，调用远端 YOLO 模型，输出标注图、结构化结果、置信度和 AI 推测说明。</p>
        </div>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={loadTasks}>刷新任务</Button>
        </Space>
      </div>

      <Alert
        type="success"
        showIcon
        message="远端 YOLO 推理已接入"
        description="当前页面会把图片发给 192.168.1.2 上的实际模型服务。若模型无法稳定识别具体类别，会退回到区域提议模式，至少圈出疑似零部件区域。"
      />

      <Row gutter={16}>
        <Col span={8}>
          <Card title={<Space><SettingOutlined />推理参数</Space>}>
            <Space direction="vertical" size={16} style={{ width: "100%" }}>
              <div>
                <Typography.Text>置信度阈值</Typography.Text>
                <Slider min={0.05} max={0.9} step={0.01} value={confidence} onChange={(value) => setConfidence(Array.isArray(value) ? value[0] : value)} />
              </div>
              <div>
                <Typography.Text>IoU 阈值</Typography.Text>
                <Slider min={0.1} max={0.95} step={0.01} value={iou} onChange={(value) => setIou(Array.isArray(value) ? value[0] : value)} />
              </div>
              <div>
                <Typography.Text>输入尺寸</Typography.Text>
                <Slider min={320} max={1280} step={32} value={imageSize} onChange={(value) => setImageSize(Array.isArray(value) ? value[0] : value)} />
              </div>
            </Space>
          </Card>
        </Col>
        <Col span={16}>
          <Card>
            <Space direction="vertical" size={16} style={{ width: "100%" }}>
              <Upload.Dragger
                accept="image/*"
                showUploadList={false}
                beforeUpload={(file) => {
                  void analyzeFile(file);
                  return false;
                }}
              >
                <p className="ant-upload-drag-icon"><InboxOutlined /></p>
                <p className="ant-upload-text">点击或拖拽底盘图片到此区域</p>
                <p className="ant-upload-hint">建议上传清晰的底盘局部图、悬架总成图或轮端细节图。</p>
              </Upload.Dragger>
            </Space>
          </Card>
        </Col>
      </Row>

      <div className="two-column" style={{ marginTop: 16 }}>
        <Space direction="vertical" size={16} style={{ width: "100%" }}>
          <Card title="识别任务">
            <Table
              rowKey="id"
              size="small"
              pagination={false}
              dataSource={tasks}
              columns={[
                { title: "任务 ID", dataIndex: "id", width: 90 },
                { title: "文件", dataIndex: "file_name" },
                { title: "状态", dataIndex: "status", render: (value) => <Tag color="green">{value}</Tag> },
                { title: "Detector", dataIndex: "detector_name" },
                { title: "对象数", dataIndex: "object_count" },
              ]}
            />
          </Card>
        </Space>

        <Space direction="vertical" size={16} style={{ width: "100%" }}>
          <Card title="标注预览" loading={loading}>
            {result ? (
              <Image src={result.annotated_image} alt="底盘识别标注图" width="100%" />
            ) : (
              <Empty description="上传图片后显示标注结果" />
            )}
          </Card>
          <Card title="AI 推测说明">
            {result ? (
              <Descriptions bordered column={1}>
                <Descriptions.Item label="图片尺寸">{result.image.width} x {result.image.height}</Descriptions.Item>
                <Descriptions.Item label="识别数量">{result.detections.length}</Descriptions.Item>
                <Descriptions.Item label="推测说明">{result.ai_summary}</Descriptions.Item>
              </Descriptions>
            ) : (
              <Empty description="暂无推测说明" />
            )}
          </Card>
        </Space>
      </div>

      <Card title="识别结果">
        <Table
          rowKey="id"
          dataSource={result?.detections || []}
          columns={[
            { title: "零部件", dataIndex: "label" },
            { title: "实体编码", dataIndex: "entity_type_code" },
            { title: "系统", dataIndex: "system_name", render: (value) => value || "-" },
            { title: "置信度", dataIndex: "confidence", render: (value) => `${Math.round(value * 100)}%` },
            { title: "位置 bbox", dataIndex: "bbox", render: (value: number[]) => value.join(", ") },
            { title: "来源", dataIndex: "source" },
            { title: "复核状态", dataIndex: "review_status", render: (value) => <Tag color={reviewColor(String(value))}>{value}</Tag> },
            { title: "推测依据", dataIndex: "reasoning" },
          ]}
        />
      </Card>
    </div>
  );
}
