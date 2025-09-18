import {RE2JS} from "re2js";
import * as url_template_lib from "url-template";
import type * as z from "zod/mini";

import * as blueslip from "./blueslip.ts";
import type {realm_linkifier_schema} from "./state_data.ts";

type LinkifierMap = Map<RE2JS, url_template_lib.Template>;
const linkifier_map: LinkifierMap = new Map();

type Linkifier = z.output<typeof realm_linkifier_schema>;

export function get_linkifier_map(): LinkifierMap {
    return linkifier_map;
}

export function compile_linkifier(
    pattern: string,
    url: string,
): [RE2JS, url_template_lib.Template] {
    // This boundary-matching must be kept in sync with prepare_linkifier_pattern in
    // zerver.lib.markdown.  It does not use look-ahead or look-behind, because re2
    // does not support either.
    pattern = "(^|\\s|\\u0085|\\p{Z}|['\"(,:<])(" + pattern + ")($|[^\\p{L}\\p{N}])";
    const compiled_regex = RE2JS.compile(pattern);
    const url_template = url_template_lib.parseTemplate(url);
    return [compiled_regex, url_template];
}

export function update_linkifier_rules(linkifiers: Linkifier[]): void {
    linkifier_map.clear();

    for (const linkifier of linkifiers) {
        try {
            const [regex, url_template] = compile_linkifier(
                linkifier.pattern,
                linkifier.url_template,
            );
            linkifier_map.set(regex, url_template);
        } catch (error) {
            // We have an error computing the generated regex syntax.
            // We'll ignore this linkifier for now, but log this
            // failure for debugging later.
            blueslip.error("Failed to compile linkifier!", linkifier, error);
        }
    }
}

export function initialize(linkifiers: Linkifier[]): void {
    update_linkifier_rules(linkifiers);
}
