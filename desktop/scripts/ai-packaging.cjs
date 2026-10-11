function aiPackagingArgs() {
  const packages = [
    "deepagents", "langchain", "langchain_core", "langchain_openai", "langchain_anthropic",
    "langgraph", "langsmith", "wcmatch", "bracex",
    "pypdf", "pypdfium2", "pypdfium2_raw", "tiktoken", "docx", "pptx", "openpyxl",
    "onnxruntime", "rapidocr", "tokenizers", "sqlite_vec", "huggingface_hub", "flatbuffers"
  ];
  const args = packages.flatMap((name) => ["--collect-all", name]);
  args.push("--recursive-copy-metadata", "rapidocr");
  // External DirectML workers verify these application-provided dependencies before importing ORT.
  for (const name of ["flatbuffers", "numpy", "packaging", "protobuf"]) args.push("--copy-metadata", name);
  args.push("--collect-submodules", "tiktoken_ext", "--hidden-import", "langgraph.checkpoint.sqlite.aio");
  return args;
}

module.exports = { aiPackagingArgs };
