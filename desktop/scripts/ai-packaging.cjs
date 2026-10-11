function aiPackagingArgs() {
  const packages = [
    "deepagents", "langchain", "langchain_core", "langchain_openai", "langchain_anthropic",
    "langgraph", "langsmith", "wcmatch", "bracex",
    "pypdf", "pypdfium2", "pypdfium2_raw", "tiktoken", "docx", "pptx", "openpyxl",
    "onnxruntime", "tokenizers", "sqlite_vec", "huggingface_hub"
  ];
  const args = packages.flatMap((name) => ["--collect-all", name]);
  args.push("--collect-submodules", "tiktoken_ext", "--hidden-import", "langgraph.checkpoint.sqlite.aio");
  return args;
}

module.exports = { aiPackagingArgs };
