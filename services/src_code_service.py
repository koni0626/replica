class SrcCodeService(object):
    def __init__(self, dir_path):
        self.dir_path = dir_path

    def stream_generate(project_id: int):
        data = request.get_json(silent=True) or {}
        prompt = (data.get("prompt") or "").strip()
        if not prompt:
            return jsonify({"error": "prompt is required"}), 400

        provider = GptProvider()
        svc = DocService()

        def generate():
            yield ""
            for piece in provider.stream_with_history(project_id=project_id, prompt=prompt, svc=svc, history_limit=20):
                yield piece

        return Response(stream_with_context(generate()), mimetype="text/plain; charset=utf-8")