import Markdown, { type Components } from "react-markdown"
import remarkGfm from "remark-gfm"
import { AiCodeBlock } from "./AiCodeBlock"

const components: Components = {
  pre: ({ node, children }) => {
    const code = node?.children[0]
    if (code?.type === "element" && code.tagName === "code") {
      const text = code.children.map(child => child.type === "text" ? child.value : "").join("").replace(/\n$/, "")
      const classes = code.properties.className
      const language = (Array.isArray(classes) ? classes.join(" ") : String(classes || "")).match(/language-([\w-]+)/)?.[1] || "text"
      return <AiCodeBlock text={text} language={language} />
    }
    return <pre>{children}</pre>
  },
  a: ({ href, children, title }) => href ? <a href={href} title={title} target={href.startsWith("#") ? undefined : "_blank"} rel="noopener noreferrer">{children}</a> : <span>{children}</span>,
  // Keep provider-supplied remote resources opt-in, like other external links.
  img: ({ src, alt }) => src ? <a href={src} target="_blank" rel="noopener noreferrer">{alt || "View image"}</a> : <span>{alt}</span>,
  table: ({ children }) => <div className="max-w-full overflow-x-auto"><table>{children}</table></div>,
}

export function AiMarkdown({ text }: { text: string }) {
  return <div className="ai-markdown min-w-0 text-[13px] leading-relaxed text-on-surface-variant">
    <Markdown remarkPlugins={[remarkGfm]} components={components} skipHtml>{text}</Markdown>
  </div>
}
