# Build the deep-learning computation-graph figures from TikZ sources.
#
# Each <name>.tex is a standalone TikZ picture. Compiling produces <name>.pdf,
# which is written to the parent dir (figs/deep_learning/) under the exact
# filename the guide's \includegraphics already references. The web build
# (build_web.py) then converts those PDFs to SVG automatically.
#
#   make        # rebuild all figures
#   make clean  # remove LaTeX aux files

FIGS = LogLin LogLin_color NN2 NN_backprop_colored2 RNN2 RNN2_backprop
OUT  = ..
PDFS = $(addprefix $(OUT)/,$(addsuffix .pdf,$(FIGS)))

all: $(PDFS)

$(OUT)/%.pdf: %.tex nn_style.tex
	pdflatex -interaction=nonstopmode -halt-on-error $<
	mv $*.pdf $(OUT)/$*.pdf
	$(MAKE) clean

clean:
	rm -f *.aux *.log *.out

.PHONY: all clean
