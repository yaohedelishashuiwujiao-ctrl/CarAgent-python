import { useEffect, useMemo, useState } from "react";
import type { Key } from "react";
import { Button, Card, Descriptions, Empty, Input, Space, Table, Tag, Tree } from "antd";
import type { DataNode } from "antd/es/tree";

import { AssetTreeNode, AttributeValueDetail, VehicleDetailResponse, fetchJson } from "../api";

type TreeNode = DataNode & {
  raw: AssetTreeNode;
  children?: TreeNode[];
};

function nodeKey(node: AssetTreeNode) {
  return `${node.node_type}:${node.code || node.id}`;
}

function nodeTitle(node: AssetTreeNode) {
  const color = node.node_type === "vehicle_series" ? "blue" : node.node_type === "vehicle" ? "green" : node.node_type === "system_profile" ? "gold" : "purple";
  const label = node.node_type === "vehicle_series" ? "车系" : node.node_type === "vehicle" ? "版本" : node.node_type === "system_profile" ? "系统" : "零部件";
  return (
    <Space size={6}>
      <Tag color={color}>{label}</Tag>
      <span>{node.title}</span>
    </Space>
  );
}

function toTreeNode(node: AssetTreeNode): TreeNode {
  return {
    key: nodeKey(node),
    title: nodeTitle(node),
    isLeaf: node.node_type === "component",
    raw: node,
    children: node.children?.map(toTreeNode),
  };
}

function updateTree(nodes: TreeNode[], key: string, children: TreeNode[]): TreeNode[] {
  return nodes.map((node) => {
    if (node.key === key) return { ...node, children };
    if (node.children) return { ...node, children: updateTree(node.children, key, children) };
    return node;
  });
}

function findNode(nodes: TreeNode[], key?: string): TreeNode | undefined {
  if (!key) return undefined;
  for (const node of nodes) {
    if (node.key === key) return node;
    const child = findNode(node.children || [], key);
    if (child) return child;
  }
  return undefined;
}

function formatValue(item: AttributeValueDetail) {
  if (item.value === null || item.value === undefined || item.value === "") return "-";
  return `${String(item.value)}${item.unit ? ` ${item.unit}` : ""}`;
}

export function StructureTreePage() {
  const [tree, setTree] = useState<TreeNode[]>([]);
  const [selectedKey, setSelectedKey] = useState<string>();
  const [keyword, setKeyword] = useState("");
  const [fieldKeyword, setFieldKeyword] = useState("");
  const [detail, setDetail] = useState<VehicleDetailResponse | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);

  const loadRoot = (query = keyword) => {
    const params = new URLSearchParams();
    if (query.trim()) params.set("keyword", query.trim());
    void fetchJson<AssetTreeNode[]>(`/api/assets/tree/lazy?${params.toString()}`).then((items) => {
      setTree(items.map(toTreeNode));
      setSelectedKey(undefined);
      setDetail(null);
    });
  };

  useEffect(() => {
    loadRoot("");
  }, []);

  const selected = useMemo(() => findNode(tree, selectedKey), [tree, selectedKey]);
  const filteredGroups = useMemo(() => {
    if (!detail) return [];
    const q = fieldKeyword.trim().toLowerCase();
    if (!q) return detail.groups;
    return detail.groups
      .map((group) => ({
        ...group,
        values: group.values.filter((item) => `${item.attribute_name} ${item.attribute_code} ${String(item.value ?? "")}`.toLowerCase().includes(q)),
      }))
      .filter((group) => group.values.length > 0);
  }, [detail, fieldKeyword]);

  async function loadChildren(node: TreeNode) {
    if (node.children && node.children.length > 0) return;
    const raw = node.raw;
    const params = new URLSearchParams({ parent_type: raw.node_type, parent_id: raw.node_type === "vehicle_series" ? raw.code : String(raw.instance_id || raw.id) });
    const children = await fetchJson<AssetTreeNode[]>(`/api/assets/tree/lazy?${params.toString()}`);
    setTree((items) => updateTree(items, String(node.key), children.map(toTreeNode)));
  }

  async function handleSelect(keys: Key[]) {
    const key = keys[0]?.toString();
    setSelectedKey(key);
    setFieldKeyword("");
    const node = findNode(tree, key);
    if (!node || node.raw.node_type !== "vehicle") {
      setDetail(null);
      return;
    }
    setLoadingDetail(true);
    try {
      setDetail(await fetchJson<VehicleDetailResponse>(`/api/assets/vehicles/${node.raw.instance_id}`));
    } finally {
      setLoadingDetail(false);
    }
  }

  return (
    <div className="page">
      <div className="page-title">
        <div>
          <h2>结构树查询</h2>
          <p>按车系、车型版本、虚拟系统、零部件逐级加载；点击车型版本后按分组显示真实动态属性值。</p>
        </div>
        <Space>
          <Input.Search
            allowClear
            placeholder="搜索车系 / 车型 / specid"
            style={{ width: 320 }}
            onSearch={(value) => {
              setKeyword(value);
              loadRoot(value);
            }}
            onChange={(event) => {
              setKeyword(event.target.value);
              if (!event.target.value) loadRoot("");
            }}
          />
          <Button onClick={() => loadRoot(keyword)}>刷新</Button>
        </Space>
      </div>

      <div className="two-column">
        <Card title="实例结构树">
          <Tree
            showLine
            treeData={tree}
            loadData={(node) => loadChildren(node as TreeNode)}
            selectedKeys={selectedKey ? [selectedKey] : []}
            onSelect={handleSelect}
          />
        </Card>

        <Card
          title="节点数据"
          extra={
            detail ? (
              <Input.Search
                allowClear
                placeholder="搜索属性 / 值"
                style={{ width: 240 }}
                onSearch={setFieldKeyword}
                onChange={(event) => setFieldKeyword(event.target.value)}
              />
            ) : null
          }
        >
          {!selected ? (
            <Empty description="请选择左侧节点" />
          ) : (
            <Space direction="vertical" size={16} style={{ width: "100%" }}>
              <Descriptions bordered column={1} size="small">
                <Descriptions.Item label="节点类型">
                  <Tag>{selected.raw.node_type}</Tag>
                </Descriptions.Item>
                <Descriptions.Item label="名称">{selected.raw.title}</Descriptions.Item>
                <Descriptions.Item label="编码">{selected.raw.code}</Descriptions.Item>
              </Descriptions>

              {selected.raw.node_type !== "vehicle" ? (
                <Empty description={selected.raw.node_type === "vehicle_series" ? "展开车系查看车型版本" : "展开节点查看下级数据"} />
              ) : (
                <Space direction="vertical" size={16} style={{ width: "100%" }}>
                  <Descriptions bordered column={2} size="small">
                    <Descriptions.Item label="车型版本">{detail?.vehicle.vehicle_name || selected.raw.title}</Descriptions.Item>
                    <Descriptions.Item label="实例编码">{detail?.vehicle.vehicle_code || selected.raw.code}</Descriptions.Item>
                    <Descriptions.Item label="来源">{detail?.vehicle.source_type || "-"}</Descriptions.Item>
                    <Descriptions.Item label="属性值">{detail ? detail.groups.reduce((sum, group) => sum + group.values.length, 0) : "-"}</Descriptions.Item>
                  </Descriptions>

                  {filteredGroups.map((group) => (
                    <Card key={group.group_code} size="small" title={`${group.group_name} (${group.values.length})`}>
                      <Table
                        rowKey="attribute_id"
                        loading={loadingDetail}
                        size="small"
                        pagination={{ pageSize: 12, showSizeChanger: false }}
                        dataSource={group.values}
                        columns={[
                          { title: "属性", dataIndex: "attribute_name", width: 220 },
                          { title: "编码", dataIndex: "attribute_code", width: 180 },
                          { title: "类型", dataIndex: "attr_type", width: 90 },
                          { title: "值", render: (_, row) => formatValue(row) },
                          { title: "来源", dataIndex: "source", width: 100, render: (value) => <Tag>{value}</Tag> },
                        ]}
                      />
                    </Card>
                  ))}
                </Space>
              )}
            </Space>
          )}
        </Card>
      </div>
    </div>
  );
}
