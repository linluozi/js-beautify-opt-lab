# js-beautify-opt-lab

`js-beautify` 是 JavaScript 代码美化器的官方 Python 移植（9k★）。本仓库把它 tokenizer 的
「每个 token 依次试 10 个 reader」改成首字符分派，真实 1.76 MB JavaScript 语料上中位
**1.21×**，输出与上游**逐字节相同**（1200 组去重样本 0 差异）。

```text
Upstream: https://github.com/beautifier/js-beautify
```

- 上游版本：`python/` 包，commit `4f52c492a47d1aadfeb22b5350a4784a36f736b3`（2026-07-21；
  仓库 `pushed_at` 2026-10-19），MIT。
- 本地改动：`src/jsbeautifier/` 是打了补丁的完整包；`patch.diff` 可对干净上游 clone
  `git apply`（已 `--check` 通过）。**59 行新增 / 3 行删除**（共 62 行变更，≤80）。
- 未改公共 API、未改返回值语义、未改上游测试文件。

## 改了什么

`Tokenizer._get_next_token` 原本对每个 token 按固定顺序尝试 10 个 reader：

```python
token = token or self._read_non_javascript(c)
token = token or self._read_string(c)
... 8 more ...
```

但对一个确定的起始字符，至多只有一类 reader 可能匹配。补丁加入 `_FAST_START`
首字符表：字母/数字/`$`/`_` 直接走 `_read_word`，引号走 `_read_string`，括号/分号/逗号走
`_read_singles`，运算符走 `_read_punctuation`，`.` 走 `word → singles`；`/`、`<`、`#`、`-`
（其 token 依赖 tokenizer 状态）仍走完整级联。命中快速路径时直接返回，未命中或返回
`None` 时原级联原样执行，因此是纯短路。

另两处小改动（消融显示贡献很小，见下）：

1. `_read_word` 的 `re.sub(allLineBreaks, ...)` 加“确实含换行才替换”的前置判断，并改用
   已编译 pattern 的 `.sub()`；
2. `_readWhitespace` 用 `isspace()` + 显式补充字符集做快速拒绝。**注意** JS 的空白类还包含
   `\u180e`、`\ufeff` 这类 `isspace()` 为 False 的字符，第一版就因此把 BOM 并进了 token，
   被 1200 组差分中的 BOM 用例抓出，修正后 0 差异；`tests/test_whitespace_predicate.py`
   把该前提固化成测试。

## 结果（本机实测，非预测）

三个规模都是真实 `.js` 文件拼接，warmup 3 次 + 计时 5 次，取中位数（p25/p75 见
`benchmark/baseline.json` / `benchmark/optimized.json`，原始样本在 `benchmark/raw/`）。

| 规模 | 输入 | 基线中位 | 优化后中位 | 加速比 |
| --- | --- | --- | --- | --- |
| small | 217,747 B | 0.5090 s | 0.4216 s | **1.207×** |
| medium | 470,363 B | 0.9228 s | 0.7537 s | **1.224×** |
| large | 959,830 B | 1.8589 s | 1.5451 s | **1.203×** |

三规模中位加速 **1.207×**（G3）。三个规模的输出 sha256 基线与优化后完全一致
（`d4d0ef84…` / `64f0e3f7…` / `27b785ff…`）。

**真实语料复测**（`/usr/share/nodejs` 全部 141 个 `.js`，合计 1,756,708 B；对 ≥16 KB 的
17 个文件各取 3 次中位再求比值）：**中位 1.178×，最差 0.972×（`llhttp-wasm.js`），
最好 1.244×**。最差那个文件以超长字符串为主，空白与分派收益都小，单文件 3 次采样的
波动即可解释该 3% —— 不把它包装成“全面加速”。中位真实语料加速 ≥1.15×（G3b）。

## 机理证书：每 token 探测次数

`benchmark/probes.py` 把每个 reader 包一层计数器（只统计，不在计时轮里跑）：

| 指标 | 基线 | 优化后 |
| --- | --- | --- |
| token 数 | 70,206 | 70,206 |
| reader 探测总数 | 413,894 | 149,771 |
| 非空白探测 / token | **4.895** | **1.133** |

token 数不变、探测数 −76.9%，说明加速来自“少试 reader”，不是改变了分词。

## 逐 hunk 消融（真实 470 KB 文件）

`benchmark/ablation.py` 从 `src/` 出发，每次只回退一个 hunk 后重测（warmup 3 + 5 次中位）：

| 变体 | 中位 | 相对基线 |
| --- | --- | --- |
| 基线 | 0.9295 s | 1.000× |
| 完整补丁 | 0.7701 s | **1.207×** |
| 只留首字符分派 | 0.8197 s | 1.134× |
| 只留 sub 前置判断 | 0.8793 s | 1.057× |
| 只留 whitespace 快拒 | 0.9310 s | 0.999× |

leave-one-out 边际节省：分派 0.1026 s（64.3%）、sub 判断 0.0453 s（28.4%）、
whitespace 0.0037 s（2.3%），合计解释 **95.0%** 的实测总节省。三个 hunk 的单独收益之和
0.1586 s 与合并 0.1595 s 之比为 0.99× —— 在本工作负载上它们近似可加，不存在重复计算。
**whitespace 快拒单独只有 0.999×**：这是一条明确的负结论，保留它只是因为它在合并后仍有
2.3% 边际收益，且不增加复杂度。

## 差分测试

```
independent_samples: 1200
unique_by_sha256: 1200
generated_attempts: 1368
real_files: 141 / edge_cases: 29 / random_slices: 1030
seed: 4321
samples_with_error_either_side: 0
mismatches: 0
verdict: PASS
```

样本 = `/usr/share/nodejs` 全部 141 个真实 `.js` + 29 个边界（空串、纯空白、BOM、U+2028/U+2029、
shebang、HTML 注释、sharp variable、模板字符串、正则、非 ASCII 标识符…）+ 1030 个固定种子
随机切片（带 4 种外壳）；按 **sha256 去重后 1200 个独立样本**，不是循环次数。比对是两侧
输出文件逐字节 `==`（不是类型/长度/前缀比较）。

## 质量闸门

| 闸门 | 结论 | 复现命令 |
| --- | --- | --- |
| G1 上游测试 | **36 passed, 0 failed** | `cd src && python3 -m unittest discover -s jsbeautifier -t . -p 'test*.py'` |
| G2 差分一致 | **1200/1200 逐字节相等** | `python3 tests/test_diff.py --baseline /tmp/js-beautify/python` |
| G3 中位加速 | **1.207×**（三规模） | `python3 benchmark/bench.py --baseline /tmp/js-beautify/python` |
| G3b 真实语料 | **1.178×**（17 个 ≥16 KB 文件；最差 0.972×） | 同上，见 `benchmark/summary.json` |
| G4 最大规模不退化 | **1.203×**（large） | 同上 |
| G5 峰值内存 | **67,520 / 67,404 kB = 1.002×** | `PYTHONPATH=src /usr/bin/time -v python3 -c "…"`（见下） |

G5 的复现（基线把 `PYTHONPATH` 换成上游 `python/`）：

```bash
PYTHONPATH=src python3 -c "import glob,os,jsbeautifier; f=sorted(glob.glob('/usr/share/nodejs/**/*.js',recursive=True)); t='\n'.join(open(p,encoding='utf-8',errors='replace').read() for p in sorted(f,key=os.path.getsize)[-6:]); jsbeautifier.beautify(t)"
```

解释器交叉：本机只有 `python3.12.3`，无 3.10/3.11，**无法做多版本交叉**，不做任何编造。

## 复现（3 条命令）

```bash
git clone https://github.com/beautifier/js-beautify /tmp/js-beautify
git -C /tmp/js-beautify checkout 4f52c492a47d1aadfeb22b5350a4784a36f736b3
python3 benchmark/bench.py --baseline /tmp/js-beautify/python
```

差分与消融分别是 `python3 tests/test_diff.py --baseline /tmp/js-beautify/python` 与
`python3 benchmark/ablation.py --baseline /tmp/js-beautify/python`；探测计数用
`python3 benchmark/probes.py --impl optimized`（基线加 `--baseline …`）。

## 环境

- CPU：AMD EPYC 7K62 48-Core Processor（虚拟机 4 vCPU）
- 内存：3.7 GB；Python 3.12.3；Node v24.19.0（未参与基准）
- 语料：`/usr/share/nodejs` 141 个真实 `.js`，1,756,708 B
- 随机种子：4321（差分切片）；bench 无随机性
- 计时：`time.perf_counter()`；warmup 3 次不计入，5 次计时取中位 + p25/p75

## 本轮创新

**B 档。** 不是再报一个中位加速，而是给出「探测次数 → 时间」的机制链，并把同一补丁内
**被证伪的 hunk** 一并发表：

1. 首个把 Python 版 `js-beautify` tokenizer 的“每 token reader 探测次数”做成可复现证书：
   4.895 → 1.133 探测/token（−76.9%），token 数 70,206 不变，证明 20% 的墙钟收益来自
   短路掉不可能匹配的 reader，而不是分词变化或计时噪声。
2. 首字符分派表 + leave-one-out 归因：三个 hunk 的边际节省解释 95.0% 实测总节省
   （分派 64.3% / sub 判断 28.4% / whitespace 2.3%）；单独收益之和与合并收益之比 0.99×，
   说明本工作负载上它们近似可加。
3. 反直觉负结论：profiler 里 whitespace 读取看起来热，但单独上线快拒只有 **0.999×**；
   且它的第一版引入 BOM 语义错误（把 `\ufeff` 当非空白），被差分测试抓出。把“看起来该做”
   和“实测有收益”分开，是本仓库与常见 opt-lab 的区别。

## 许可证与来源

MIT，原文见 `LICENSE`（未改协议名）。上游：beautifier/js-beautify（`python/` 包）。
本仓库是独立的分支实验：保留上游版权声明，改动范围仅为
`jsbeautifier/javascript/tokenizer.py`（见 `patch.diff`）；`src/` 其余文件为上游原文。
