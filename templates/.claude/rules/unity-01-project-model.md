# Unity 项目模型规范

Editor 代码与 Runtime 代码必须分离；不得手动编辑生成的 View 文件；必须保留 `.meta` 文件标识。Prefab、Scene、生成资产、导入资产和序列化引用均属于高风险改动，必须提供验证证据。
