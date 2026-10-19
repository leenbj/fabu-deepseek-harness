#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把生成好的网页上传到 FTP 服务器（用户授权的交付动作，不是发布）。

只上传 `--pattern` 匹配的文件（默认 `*.html`）——**默认不上传 `_pages.json`**，
因为清单里记的是本机绝对路径，放到 Web 目录里等于把本地目录结构公开出去。

用法：

    python ftp_upload.py --local-dir "<本地目录>" --remote-dir "/article-detail" \\
        --host <FTP 主机> --user <FTP 账号> --password-env FTP_PASS

    python ftp_upload.py --local-dir "<本地目录>" --dry-run     # 只报将要上传什么，不连接不改动
    python ftp_upload.py --local-dir "<本地目录>" --skip-existing

目标服务器的**非敏感**信息可以落到一份 JSON 里反复使用（主机／端口／账号／远端目录），
默认自动读 `~/.dsh/ftp-target.json`，也可以用 `--config` 指定，或 `--save-config` 从一次
显式调用里存下来：

    {"host": "ftp.example.com", "port": 21, "user": "<FTP 账号>",
     "remote_dir": "/article-detail", "password_env": "FTP_PASS"}

**密码永远不进这份配置**：只从 `--password` 或 `--password-env` 指定的环境变量取。
上传后用远端 SIZE 与本地字节数核对，不一致即报失败。

注意（实测踩过的坑）：这类服务器常在云安全组里只放行了 21 端口，
被动模式的数据端口（39000-40000 一段）没放行时会表现为「登录成功但一传就超时」。
遇到 `TimeoutError` 先让服务器侧放行被动端口段，再重试。

退出码：0 = 全部上传并核对通过，1 = 有失败项，2 = 用法错误。
"""
from __future__ import annotations

import argparse
import ftplib
import json
import os
import sys
from pathlib import Path

DEFAULT_CONFIG = Path.home() / ".dsh" / "ftp-target.json"


class NatSafeFTP(ftplib.FTP):
    """PASV 返回内网地址时，改连控制连接用的主机名 + 它给的端口。"""

    def makepasv(self):  # type: ignore[override]
        _host, port = super().makepasv()
        return self.host, port


def ensure_remote_dir(ftp: ftplib.FTP, remote_dir: str) -> None:
    """逐级创建远端目录（已存在不算错）。"""
    parts = [p for p in remote_dir.strip("/").split("/") if p]
    cur = ""
    for part in parts:
        cur = f"{cur}/{part}"
        try:
            ftp.mkd(cur)
            print(f"  新建远端目录 {cur}")
        except ftplib.error_perm as exc:
            if not str(exc).startswith("550"):
                raise


def remote_size(ftp: ftplib.FTP, path: str) -> int | None:
    try:
        ftp.voidcmd("TYPE I")
        return ftp.size(path)
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description="把网页上传到 FTP 服务器并核对大小")
    ap.add_argument("--local-dir", type=Path, required=True, help="要上传的本地目录")
    ap.add_argument("--remote-dir", help="远端目录（不存在会逐级创建），默认 /")
    ap.add_argument("--host", help="FTP 主机（也可写在 --config 里）")
    ap.add_argument("--port", type=int, help="FTP 端口，默认 21")
    ap.add_argument("--user", help="FTP 账号（也可写在 --config 里）")
    ap.add_argument("--password", help="FTP 密码（不建议写在命令行里）")
    ap.add_argument("--password-env", dest="password_env",
                    help="从哪个环境变量取密码，默认 FTP_PASS")
    ap.add_argument("--config", type=Path,
                    help=f"非敏感目标配置 JSON（默认尝试 {DEFAULT_CONFIG}；不含密码）")
    ap.add_argument("--save-config", type=Path,
                    help="把本次的主机/端口/账号/远端目录写进该 JSON（不含密码）后退出前的存档")
    ap.add_argument("--pattern", default="*.html",
                    help="只上传匹配的文件，默认 *.html（不上传 _pages.json）")
    ap.add_argument("--encoding", default="utf-8", help="控制连接编码，默认 utf-8")
    ap.add_argument("--timeout", type=int, default=30)
    ap.add_argument("--skip-existing", action="store_true", help="远端已有同名文件就跳过")
    ap.add_argument("--dry-run", action="store_true", help="只列出将要上传的文件")
    args = ap.parse_args()

    # --- 合并配置：命令行 > --config > 默认路径 > 内置默认 ---------------------
    cfg: dict = {}
    cfg_path = args.config or (DEFAULT_CONFIG if DEFAULT_CONFIG.exists() else None)
    if cfg_path:
        try:
            cfg = json.loads(Path(cfg_path).read_text(encoding="utf-8"))
            print(f"配置    : {cfg_path}")
        except Exception as exc:
            print(f"配置读取失败（{cfg_path}）：{exc}", file=sys.stderr)
            return 2

    host = args.host or cfg.get("host")
    user = args.user or cfg.get("user")
    port = args.port or cfg.get("port") or 21
    remote_dir_arg = args.remote_dir if args.remote_dir is not None else cfg.get("remote_dir", "/")
    password_env = args.password_env or cfg.get("password_env") or "FTP_PASS"

    if not host or not user:
        print("缺少主机或账号：用 --host/--user，或写进 --config 指向的 JSON", file=sys.stderr)
        return 2

    if args.save_config:
        saved = {"host": host, "port": port, "user": user,
                 "remote_dir": remote_dir_arg, "password_env": password_env}
        args.save_config.parent.mkdir(parents=True, exist_ok=True)
        args.save_config.write_text(json.dumps(saved, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
        print(f"已保存目标配置（不含密码）→ {args.save_config}")

    if not args.local_dir.is_dir():
        print(f"本地目录不存在：{args.local_dir}", file=sys.stderr)
        return 2
    files = sorted(p for p in args.local_dir.glob(args.pattern) if p.is_file())
    if not files:
        print(f"{args.local_dir} 下没有匹配 {args.pattern} 的文件", file=sys.stderr)
        return 2

    remote_dir = "/" + remote_dir_arg.strip("/") if remote_dir_arg.strip("/") else "/"
    print(f"本地 {args.local_dir}  →  远端 {host}:{port}{remote_dir}")
    print(f"待上传 {len(files)} 个文件（{args.pattern}），共 "
          f"{sum(f.stat().st_size for f in files) // 1024} KB")
    for f in files:
        print(f"   - {f.name}  ({f.stat().st_size} 字节)")

    if args.dry_run:
        print("\nDRY-RUN：未连接、未写盘")
        return 0

    password = args.password or os.environ.get(password_env)
    if not password:
        print(f"没有密码：用 --password 或设置环境变量 {password_env}", file=sys.stderr)
        return 2

    ftp = NatSafeFTP()
    ok = failed = skipped = 0
    try:
        ftp.connect(host, port, timeout=args.timeout)
        ftp.login(user, password)
        ftp.encoding = args.encoding
        ftp.set_pasv(True)
        print(f"  已登录：{ftp.getwelcome().splitlines()[0][:60]}")
        ensure_remote_dir(ftp, remote_dir)

        for path in files:
            remote = f"{remote_dir.rstrip('/')}/{path.name}"
            size = path.stat().st_size
            existing = remote_size(ftp, remote)
            if existing is not None and existing == size:
                if args.skip_existing:
                    print(f"  skip  {path.name}（远端已有且大小相同）")
                    skipped += 1
                    continue
            note = "" if existing is None else f"（覆盖远端已有文件 {existing} 字节）"
            with path.open("rb") as fh:
                ftp.storbinary(f"STOR {remote}", fh)
            got = remote_size(ftp, remote)
            if got == size:
                print(f"  ok    {path.name}  {size} 字节{note}")
                ok += 1
            else:
                print(f"  FAIL  {path.name}  本地 {size} 字节，远端 {got} 字节", file=sys.stderr)
                failed += 1

        print("\n  远端目录内容：")
        try:
            for name in sorted(ftp.nlst(remote_dir)):
                print(f"   {name}")
        except Exception as exc:
            print(f"   （列目录失败：{exc}）")
    except Exception as exc:
        print(f"上传中断：{type(exc).__name__}: {exc}", file=sys.stderr)
        print("若是 TimeoutError：服务器侧很可能没有放行被动模式的数据端口段"
              "（实测为 39000-40000），放行后重试。", file=sys.stderr)
        return 1
    finally:
        try:
            ftp.quit()
        except Exception:
            try:
                ftp.close()
            except Exception:
                pass

    print(f"\nRESULT: {'PASS' if not failed else 'FAIL'} — "
          f"上传 {ok} 个，跳过 {skipped} 个，失败 {failed} 个")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
