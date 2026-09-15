//! 解析「如何拉起 Python 运行时」。
//!
//! 三种来源按优先级排列：显式指定的可执行文件 → bundle 内置解释器 → 开发态回退到
//! `uv run`。路径约定集中在这里，桌面壳只负责把仓库根与资源目录传进来。

use std::path::{Path, PathBuf};
use std::process::Command;

/// 启动方式：待执行的命令，以及它应该运行在哪个目录。
pub struct Launch {
    pub command: Command,
    pub cwd: PathBuf,
}

/// 解析启动命令。
///
/// 1. `AURORA_SIDECAR` 指定的可执行文件（发行包自带的运行时）。
/// 2. `resource_dir/sidecar/python` 内置解释器（Tauri bundle 的资源）。
/// 3. 开发态回退：在仓库根执行 `uv run --no-sync aurora runtime`。
///
/// `AURORA_ROOT` 可覆盖开发态目录，便于把前端与后端分处不同目录时联调。
pub fn runtime_command(resource_dir: &Path, dev_repo_root: &Path) -> Launch {
    if let Ok(executable) = std::env::var("AURORA_SIDECAR") {
        let path = PathBuf::from(executable);
        let cwd = path
            .parent()
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from("."));
        let mut command = Command::new(path);
        command.arg("--stdio");
        return Launch { command, cwd };
    }

    let sidecar = resource_dir.join("sidecar");
    let python = sidecar.join("python").join(if cfg!(windows) {
        "python.exe"
    } else {
        "bin/python3"
    });
    if python.exists() {
        let mut command = Command::new(python);
        command.args(["-m", "aurora.cli.main", "runtime"]);
        command.env("PYTHONPATH", sidecar.join("site-packages"));
        return Launch {
            command,
            cwd: resource_dir.to_path_buf(),
        };
    }

    let repo_root = std::env::var_os("AURORA_ROOT")
        .map(PathBuf::from)
        .unwrap_or_else(|| dev_repo_root.to_path_buf());
    let mut command = Command::new("uv");
    command.args(["run", "--no-sync", "aurora", "runtime"]);
    Launch {
        command,
        cwd: repo_root,
    }
}

/// 由 Tauri 壳 crate 的 manifest 目录推出仓库根。
///
/// 壳位于 `<repo>/src/tauri`，往上两级即仓库根；`uv run` 需要在工作区根执行。
pub fn repo_root_from(manifest_dir: &Path) -> PathBuf {
    manifest_dir
        .ancestors()
        .nth(2)
        .map(Path::to_path_buf)
        .unwrap_or_else(|| PathBuf::from("../.."))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn derives_repository_root_from_tauri_layer() {
        assert_eq!(
            repo_root_from(Path::new("/workspace/src/tauri")),
            PathBuf::from("/workspace")
        );
    }

    #[test]
    fn falls_back_to_uv_run_inside_repository() {
        // 用一个不存在的资源目录，强制走到开发态回退分支。
        let launch = runtime_command(Path::new("/nonexistent-resources"), Path::new("/workspace"));
        let expected_root = std::env::var_os("AURORA_ROOT")
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from("/workspace"));
        assert_eq!(launch.cwd, expected_root);
        assert_eq!(launch.command.get_program(), "uv");
    }
}
