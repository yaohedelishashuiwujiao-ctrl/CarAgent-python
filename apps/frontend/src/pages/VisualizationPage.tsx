import { Card, Select, Space } from "antd";

export function VisualizationPage() {
  return (
    <div className="page">
      <div className="page-title">
        <div>
          <h2>数据可视化</h2>
          <p>面向车型、系统、零部件做参数对比、重量分布、材料分布和竞品雷达图。</p>
        </div>
        <Space>
          <Select defaultValue="weight" options={[{ value: "weight", label: "重量对比" }, { value: "material", label: "材料分布" }]} />
          <Select defaultValue="component" options={[{ value: "vehicle", label: "整车" }, { value: "system", label: "系统" }, { value: "component", label: "零部件" }]} />
        </Space>
      </div>
      <Card title="图表区域">
        <div className="chart-placeholder">ECharts 可视化区域</div>
      </Card>
    </div>
  );
}
