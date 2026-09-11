// Copyright (c) 2026, Frappe Technologies and contributors
// License: MIT. See LICENSE

frappe.ui.form.on("Flow Run", {
	refresh(frm) {
		if (frm.doc.output) {
			const trimmed = frm.doc.output.trim();
			if (trimmed !== frm.doc.output) {
				frm.doc.output = trimmed;
				frm.refresh_field("output");
			}
		}

		const parse = (value) => {
			if (!value) return null;
			if (typeof value === "object") return value;
			try {
				return JSON.parse(value);
			} catch {
				return null;
			}
		};

		const usage = parse(frm.doc.usage);
		const has = (object, key) => Object.prototype.hasOwnProperty.call(object || {}, key);
		if (usage) {
			const rounds = Number(frm.doc.iterations || 0);
			const cached = Number(usage.cached_input_tokens);
			const cached_prompt = Number(usage.cached_input_covered_prompt_tokens);
			const cached_rounds = Number(usage.cached_input_reported_rounds || 0);
			usage.cached_input_available = has(usage, "cached_input_tokens");
			usage.cache_coverage = `${cached_rounds} / ${rounds}`;
			usage.cache_hit_percent =
				usage.cached_input_available && cached_prompt > 0 && cached >= 0 && cached <= cached_prompt
					? (cached / cached_prompt) * 100
					: null;
			usage.uncached_input_tokens =
				usage.cache_hit_percent == null ? null : cached_prompt - cached;

			const reasoning = Number(usage.reasoning_output_tokens);
			const reasoning_completion = Number(
				usage.reasoning_output_covered_completion_tokens
			);
			const reasoning_rounds = Number(usage.reasoning_output_reported_rounds || 0);
			usage.reasoning_output_available = has(usage, "reasoning_output_tokens");
			usage.reasoning_coverage = `${reasoning_rounds} / ${rounds}`;
			usage.reasoning_percent =
				usage.reasoning_output_available &&
				reasoning_completion > 0 &&
				reasoning >= 0 &&
				reasoning <= reasoning_completion
					? (reasoning / reasoning_completion) * 100
					: null;
			usage.visible_output_tokens =
				usage.reasoning_percent == null ? null : reasoning_completion - reasoning;
		}

		frm.get_field("detail_html").$wrapper.html(
			frappe.render_template("flow_run_detail", {
				usage,
				measurements: parse(frm.doc.measurements),
				config: parse(frm.doc.config_snapshot),
				calls: parse(frm.doc.tool_calls),
				questions: parse(frm.doc.questions),
			})
		);
	},
});
