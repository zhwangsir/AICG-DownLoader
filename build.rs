// 编译期把署名/版权写进 Windows exe 的版本资源（右键 属性 → 详细信息 可见）。
// 这是「二进制元数据署名」那一层：即便有人删了应用内文字，exe 文件属性里仍带作者。
// 非 Windows 为空操作；rc 资源编译器不可用时静默跳过，绝不阻断构建。
fn main() {
    #[cfg(windows)]
    {
        let mut res = winresource::WindowsResource::new();
        res.set("ProductName", "AIGCPannel 模型库");
        res.set("FileDescription", "AIGCPannel 模型库 — by Winery (WangZhenYu)");
        res.set("CompanyName", "Winery (WangZhenYu)");
        res.set("LegalCopyright", "Copyright (C) 2026 WangZhenYu (Winery)");
        let _ = res.compile();
    }
}
