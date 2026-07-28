import { useEffect, useState } from "react";
import { Card, Table, Tag } from "antd";

import { Role, fetchJson } from "../api";

export function PermissionPage() {
  const [roles, setRoles] = useState<Role[]>([]);

  useEffect(() => {
    void fetchJson<Role[]>("/api/permissions/roles").then(setRoles);
  }, []);

  return (
    <div className="page">
      <div className="page-title">
        <div>
          <h2>权限管理</h2>
          <p>采用 RBAC + 菜单权限 + 操作权限 + 数据范围，适配中后台和 ToC 平台常见实践。</p>
        </div>
      </div>
      <Card title="角色策略">
        <Table
          rowKey="code"
          dataSource={roles}
          columns={[
            { title: "角色编码", dataIndex: "code" },
            { title: "角色名称", dataIndex: "name" },
            { title: "数据范围", dataIndex: "data_scope", render: (value) => <Tag color="blue">{value}</Tag> },
            { title: "权限数量", dataIndex: "permissions", render: (value: string[]) => value.length },
          ]}
        />
      </Card>
    </div>
  );
}
