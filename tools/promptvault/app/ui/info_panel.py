from PySide6.QtWidgets import QTextEdit


class InfoPanel(QTextEdit):
    def __init__(self):
        super().__init__()

        self.setReadOnly(True)

    def set_generation(self, gen):

        lines = []

        lines.append(self.tr("Timestamp: {}").format(gen.timestamp))
        lines.append(self.tr("Generation time: {:.2f} sec").format(gen.generation_time))

        lines.append(
            self.tr("Favorite: {}").format(
                self.tr("★ Yes") if gen.favorite else self.tr("No")
            )
        )
        lines.append(
            self.tr("Rating: {}").format(
                "★" * gen.rating + "☆" * (5 - gen.rating)
                if gen.rating else self.tr("Not rated")
            )
        )

        lines.append("")

        lines.append(self.tr("Model: {}").format(gen.model))
        lines.append(self.tr("CFG: {}").format(gen.cfg))
        lines.append(self.tr("Steps: {}").format(gen.steps))
        lines.append(self.tr("Sampler: {}").format(gen.sampler))

        lines.append("")
        lines.append(self.tr("PROMPT:"))
        lines.append(gen.positive)

        lines.append("")
        lines.append(self.tr("NEGATIVE:"))
        lines.append(gen.negative)

        lines.append("")
        lines.append(self.tr("LORA:"))

        if gen.loras:
            for lora in gen.loras:
                lines.append(
                    f"• {lora.filename} ({lora.strength})"
                )
        else:
            lines.append(self.tr("None"))

        self.setPlainText("\n".join(lines))
