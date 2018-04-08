doc:
	-pdflatex guide
	-bibtex guide
	-pdflatex guide
	-pdflatex guide
	@echo ""
	@echo "DONE: make doc"
	@echo ""

clean:
	-rm -f *.blg *.aux *.log *.ps *.dvi **/*~ *.bbl *.glo *.gls
	-rm -f *.adx *.nlo *.nls *.out *.cut *.ain *.brf
	-rm -f *.idx *.ilg *.ind *.lof *.lot *.toc *.bak
	-rm -f *.tdo *.loa
	-rm -f _autidx_.*

clean-pdfs:
	-rm -f *.pdf
